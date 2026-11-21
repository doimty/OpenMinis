#!/usr/bin/env python3
"""Root packaging fixture tests. No compiler, signing tool or device is used."""
from __future__ import annotations

import json
import plistlib
import stat
import struct
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import package_ios15_root_ipa as root
from package_ios15_ipa import PackageError, load_plist, package_app
from test_package_ios15_ipa import make_app, write_plist

COMMIT = "b" * 40
RUN_ID = "44859280763"
MACHO = struct.pack("<IiiIIIII", 0xfeedfacf, 0x100000c, 0, 2, 0, 0, 0, 0)


class RootIdentityTests(unittest.TestCase):
    def test_templates_are_exact_and_share_stays_sandboxed(self):
        root.validate_templates()
        self.assertTrue(root.expected_entitlements("app")["com.apple.private.persona-mgmt"])
        share = root.expected_entitlements("share")
        self.assertNotIn("com.apple.private.persona-mgmt", share)
        self.assertNotIn("com.apple.private.security.no-sandbox", share)
        self.assertEqual(share["com.apple.security.application-groups"], [root.ROOT_GROUP])
        self.assertEqual(share["keychain-access-groups"], [root.ROOT_ID])

    def test_rejects_invalid_build_identity(self):
        for commit, run_id in (("b" * 7, RUN_ID), (COMMIT.upper(), RUN_ID),
                               (COMMIT, "0"), (COMMIT, "１２"), (COMMIT, "1\n")):
            with self.subTest(commit=commit, run_id=run_id), self.assertRaises(PackageError):
                root.validate_build_identity(commit, run_id)

    def test_identity_isolated_and_old_probe_stamp_replaced(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = make_app(Path(tmp), plugins={"MinisShare.appex": "15.0"})
            info = load_plist(app / "Info.plist")
            info.update({"MinisReentryDiagnostics": True, "MinisDiagnosticCommit": "a" * 40,
                         "MinisHostFileProbeBuild": {"schema": 1, "source_commit": "a" * 40,
                                                     "actions_run_id": "123"}})
            write_plist(app / "Info.plist", info)
            result = root.prepare_root_identity(app, COMMIT, RUN_ID)
            self.assertEqual(result["CFBundleIdentifier"], root.ROOT_ID)
            self.assertEqual(result["CFBundleDisplayName"], "Minis Root")
            self.assertTrue(result["MinisRootManagementEnabled"])
            self.assertEqual(result["MinisRootBuildCommit"], COMMIT)
            self.assertEqual(result["MinisRootActionsRunID"], RUN_ID)
            self.assertNotIn("MinisReentryDiagnostics", result)
            self.assertNotIn("MinisDiagnosticCommit", result)
            self.assertEqual(result["MinisHostFileProbeBuild"],
                             {"schema": 1, "source_commit": COMMIT, "actions_run_id": RUN_ID})
            self.assertIn("minis-root", result["CFBundleURLTypes"][-1]["CFBundleURLSchemes"])
            share = load_plist(app / "PlugIns/MinisShare.appex/Info.plist")
            self.assertEqual(share["CFBundleIdentifier"], root.ROOT_ID + ".ShareExtension")

    def test_root_source_cannot_be_repackaged_as_standard_or_files(self):
        for profile in ("standard", "broad-files"):
            with self.subTest(profile=profile), tempfile.TemporaryDirectory() as tmp:
                app = make_app(Path(tmp) / "source")
                root.prepare_root_identity(app, COMMIT, RUN_ID)
                with self.assertRaisesRegex(PackageError, "separate Root packager"):
                    package_app(app, Path(tmp) / "out", skip_sign=True, file_access_profile=profile)

    def test_share_schemes_are_isolated_while_provider_callbacks_are_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = make_app(Path(tmp))
            info = load_plist(app / "Info.plist")
            info["CFBundleURLTypes"] = [
                {"CFBundleURLName": "Claude OAuth Callback", "CFBundleURLSchemes": ["com.openminis.app"]},
                {"CFBundleURLName": "Minis Share", "CFBundleURLSchemes": ["minis"]},
                {"CFBundleURLName": "Mixed Callback", "CFBundleURLSchemes": ["minis-broadfiles", "provider-callback"]},
                {"CFBundleURLName": "MCP OAuth Callback", "CFBundleURLSchemes": ["minis-mcp"]},
                {"CFBundleURLName": "Duplicate Root", "CFBundleURLSchemes": ["MINIS-ROOT"]},
            ]
            write_plist(app / "Info.plist", info)
            result = root.prepare_root_identity(app, COMMIT, RUN_ID)
            schemes = [scheme for entry in result["CFBundleURLTypes"] for scheme in entry["CFBundleURLSchemes"]]
            self.assertEqual(schemes, ["com.openminis.app", "provider-callback", "minis-mcp", "minis-root"])
            self.assertEqual(result["CFBundleURLTypes"][0]["CFBundleURLName"], "Claude OAuth Callback")

    def test_helper_in_ordinary_source_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = make_app(Path(tmp) / "source")
            (app / root.HELPER_NAME).write_bytes(MACHO)
            with self.assertRaisesRegex(PackageError, "separate Root packager"):
                package_app(app, Path(tmp) / "out", skip_sign=True)

    def test_only_known_source_identity_is_allowed(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = make_app(Path(tmp))
            info = load_plist(app / "Info.plist")
            info["CFBundleIdentifier"] = "com.other.app"
            write_plist(app / "Info.plist", info)
            with self.assertRaises(PackageError):
                root.prepare_root_identity(app, COMMIT, RUN_ID)


class RootNativeProofTests(unittest.TestCase):
    def test_compiled_symbol_required_and_unrelated_string_not_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = make_app(Path(tmp))
            (app / "Minis").write_bytes(MACHO)
            with patch.object(root.shutil, "which", return_value="xcrun"), \
                 patch.object(root.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "0000 T _OtherSymbol\n", "")):
                with self.assertRaisesRegex(PackageError, "not compiled"):
                    root.verify_compiled_client(app)
            with patch.object(root.shutil, "which", return_value="xcrun"), \
                 patch.object(root.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "0000 T _OMRootManagementClientVersion\n", "")):
                root.verify_compiled_client(app)

    def test_debug_dylib_symbol_is_checked_and_undefined_symbol_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = make_app(Path(tmp))
            (app / "Minis").write_bytes(MACHO)
            (app / "Minis.debug.dylib").write_bytes(MACHO)
            def nm_result(command, **kwargs):
                symbol = "0000 T _OMRootManagementClientVersion\n" if command[-1].endswith("Minis.debug.dylib") else "U _OMRootManagementClientVersion\n"
                return subprocess.CompletedProcess(command, 0, symbol, "")
            with patch.object(root.shutil, "which", return_value="xcrun"), \
                 patch.object(root.subprocess, "run", side_effect=nm_result) as nm:
                root.verify_compiled_client(app)
                self.assertEqual(nm.call_count, 2)
            with patch.object(root.shutil, "which", return_value="xcrun"), \
                 patch.object(root.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "U _OMRootManagementClientVersion\n", "")):
                with self.assertRaisesRegex(PackageError, "not compiled"):
                    root.verify_compiled_client(app)

    def test_no_unsigned_escape_hatch(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(root.sys, "platform", "win32"):
            with self.assertRaisesRegex(PackageError, "requires macOS"):
                root.package_root_app(Path(tmp) / "Minis.app", Path(tmp) / "helper", Path(tmp) / "out",
                                      commit=COMMIT, run_id=RUN_ID)

    def test_fixture_ipa_contains_helper_and_keeps_source_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            app = make_app(folder / "source", plugins={"MinisShare.appex": "15.0", "Widget.appex": "16.2"})
            helper = folder / "helper"
            helper.write_bytes(MACHO)
            source_info = (app / "Info.plist").read_bytes()
            with patch.object(root.sys, "platform", "darwin"), \
                 patch.object(root, "verify_compiled_client"), \
                 patch.object(root, "audit_staged_native_floor", return_value=[{"path": root.HELPER_NAME, "minimum_os": "15.0"}]), \
                 patch.object(root, "sign_and_verify", return_value={".": root.expected_entitlements("app")}):
                manifest = root.package_root_app(app, helper, folder / "out", commit=COMMIT, run_id=RUN_ID)
            self.assertEqual((app / "Info.plist").read_bytes(), source_info)
            self.assertFalse((app / root.HELPER_NAME).exists())
            self.assertFalse(manifest["root_runtime_verified"])
            self.assertFalse(manifest["device_runtime_verified"])
            self.assertTrue(manifest["root_helper_included"])
            self.assertEqual(manifest["root_helper_operations"], ["identity", "prepare", "check", "cleanup"])
            self.assertEqual(manifest["root_helper_mode"], "0755")
            self.assertEqual(manifest["source_commit"], COMMIT)
            self.assertEqual(manifest["actions_run_id"], RUN_ID)
            self.assertEqual(manifest["host_file_probe_build"],
                             {"schema": 1, "source_commit": COMMIT, "actions_run_id": RUN_ID})
            self.assertEqual(manifest["root_helper_signed_sha256"], manifest["root_helper_archive_sha256"])
            with zipfile.ZipFile(folder / "out" / manifest["ipa_name"]) as ipa:
                self.assertIsNone(ipa.testzip())
                self.assertEqual(ipa.read(f"Payload/Minis.app/{root.HELPER_NAME}"), MACHO)
                self.assertNotIn("Payload/Minis.app/PlugIns/Widget.appex/Info.plist", ipa.namelist())
                info = plistlib.loads(ipa.read("Payload/Minis.app/Info.plist"))
                self.assertEqual(info["CFBundleIdentifier"], root.ROOT_ID)
                self.assertEqual(info["MinisHostFileProbeBuild"], manifest["host_file_probe_build"])
            self.assertEqual(json.loads((folder / "out/manifest.json").read_text()), manifest)

    def test_helper_must_be_covered_by_floor_audit(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            app = make_app(folder / "source")
            helper = folder / "helper"; helper.write_bytes(MACHO)
            with patch.object(root.sys, "platform", "darwin"), \
                 patch.object(root, "verify_compiled_client"), \
                 patch.object(root, "audit_staged_native_floor", return_value=[]), \
                 patch.object(root, "sign_and_verify") as sign:
                with self.assertRaisesRegex(PackageError, "did not cover the helper"):
                    root.package_root_app(app, helper, folder / "out", commit=COMMIT, run_id=RUN_ID)
                sign.assert_not_called()

    def test_native_floor_covers_main_debug_dylib_and_helper_and_rejects_high_helper(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = make_app(Path(tmp))
            for name in ("Minis", "Minis.debug.dylib", root.HELPER_NAME):
                (app / name).write_bytes(MACHO)
            helper_minimum = "15.0"
            def native_metadata(command, **kwargs):
                if command[1] == "lipo":
                    return subprocess.CompletedProcess(command, 0, "arm64\n", "")
                minimum = helper_minimum if command[-1].endswith(root.HELPER_NAME) else "15.0"
                output = f"Load command 0\n      cmd LC_BUILD_VERSION\n  cmdsize 32\n platform 2\n    minos {minimum}\n      sdk 26.2\n"
                return subprocess.CompletedProcess(command, 0, output, "")
            with patch.object(root.sys, "platform", "darwin"), \
                 patch.object(root.shutil, "which", return_value="xcrun"), \
                 patch.object(root.subprocess, "run", side_effect=native_metadata):
                evidence = root.audit_staged_native_floor(app)
                self.assertEqual({item["path"] for item in evidence}, {"Minis", "Minis.debug.dylib", root.HELPER_NAME})
                helper_minimum = "15.1"
                with self.assertRaisesRegex(PackageError, "minis-rootprobe.*15.1 > 15.0"):
                    root.audit_staged_native_floor(app)


class RootArchiveTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)
        self.stage = self.folder / "stage"
        self.app = make_app(self.stage / "Payload")
        self.info = root.prepare_root_identity(self.app, COMMIT, RUN_ID)
        self.helper = self.app / root.HELPER_NAME
        self.helper.write_bytes(MACHO)
        self.helper.chmod(0o644)
        self.helper_sha = root.sha256_file(self.helper)
        self.ipa = self.folder / "root.ipa"
        self.helper_member = f"Payload/Minis.app/{root.HELPER_NAME}"
        root.zip_root_payload(self.stage, self.ipa)

    def rewrite_helper(self, *, mode=None, create_system=None, data=None, duplicate=False):
        with zipfile.ZipFile(self.ipa) as archive:
            members = [(member, archive.read(member)) for member in archive.infolist()]
        with zipfile.ZipFile(self.ipa, "w") as archive:
            for member, original_data in members:
                if member.filename == self.helper_member:
                    if mode is not None:
                        member.external_attr = mode << 16
                    if create_system is not None:
                        member.create_system = create_system
                    original_data = original_data if data is None else data
                archive.writestr(member, original_data)
                if duplicate and member.filename == self.helper_member:
                    with self.assertWarns(UserWarning):
                        archive.writestr(member, original_data)

    def test_helper_is_unix_0755_even_when_source_has_no_execute_bits(self):
        root.verify_root_archive(self.ipa, self.info, self.helper_sha)
        with zipfile.ZipFile(self.ipa) as archive:
            helper = archive.getinfo(self.helper_member)
            self.assertEqual(helper.create_system, 3)
            self.assertEqual(helper.external_attr >> 16, stat.S_IFREG | 0o755)

    def test_rejects_non_executable_setuid_writable_and_symlink_modes(self):
        for mode in (stat.S_IFREG | 0o644, stat.S_IFREG | 0o4755,
                     stat.S_IFREG | 0o777, stat.S_IFLNK | 0o755):
            with self.subTest(mode=oct(mode)):
                root.zip_root_payload(self.stage, self.ipa)
                self.rewrite_helper(mode=mode)
                with self.assertRaisesRegex(PackageError, "executable mode"):
                    root.verify_root_archive(self.ipa, self.info, self.helper_sha)

    def test_rejects_non_unix_mode_metadata(self):
        self.rewrite_helper(create_system=0)
        with self.assertRaisesRegex(PackageError, "executable mode"):
            root.verify_root_archive(self.ipa, self.info, self.helper_sha)

    def test_rejects_replaced_helper_even_with_valid_zip_crc(self):
        self.rewrite_helper(data=MACHO + b"replaced-after-signing")
        with zipfile.ZipFile(self.ipa) as archive:
            self.assertIsNone(archive.testzip())
        with self.assertRaisesRegex(PackageError, "differs from the signed staging binary"):
            root.verify_root_archive(self.ipa, self.info, self.helper_sha)

    def test_rejects_duplicate_helper_members(self):
        self.rewrite_helper(duplicate=True)
        with self.assertRaisesRegex(PackageError, "exactly one helper"):
            root.verify_root_archive(self.ipa, self.info, self.helper_sha)


if __name__ == "__main__":
    unittest.main()
