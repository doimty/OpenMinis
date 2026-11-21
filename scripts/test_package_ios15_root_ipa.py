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
from pathlib import Path, PurePosixPath
from unittest.mock import patch

import package_ios15_root_ipa as root
from package_ios15_ipa import PackageError, load_plist, package_app
from test_package_ios15_ipa import make_app as make_base_app, write_plist

COMMIT = "b" * 40
RUN_ID = "44859280763"
MACHO = struct.pack("<IiiIIIII", 0xfeedfacf, 0x100000c, 0, 2, 0, 0, 0, 0)


def make_app(folder: Path, *, plugins=None, **options) -> Path:
    if plugins is None:
        plugins = {"MinisShare.appex": "15.0"}
    return make_base_app(folder, plugins=plugins, **options)


def make_native_app(folder: Path) -> Path:
    app = make_app(folder, plugins={"MinisShare.appex": "15.0",
                                   "MinisFileProvider.appex": "16.0",
                                   "AgentWidgetExtension.appex": "16.2"})
    (app / "Minis").write_bytes(MACHO)
    (app / "Minis.debug.dylib").write_bytes(MACHO)
    for plugin in root.plugin_bundles(app):
        executable = load_plist(plugin / "Info.plist")["CFBundleExecutable"]
        (plugin / executable).write_bytes(MACHO)
        (plugin / (executable + ".debug.dylib")).write_bytes(MACHO)
    framework = app / "Frameworks/Common.framework"
    write_plist(framework / "Info.plist", {"CFBundleExecutable": "Common", "MinimumOSVersion": "15.0"})
    (framework / "Common").write_bytes(MACHO)
    return app


def native_metadata(command, **options):
    if command[1] == "lipo":
        return subprocess.CompletedProcess(command, 0, "arm64\n", "")
    path = Path(command[-1])
    role = next((root.ROOT_PLUGIN_ROLES[parent.name]
                 for parent in path.parents if parent.name in root.ROOT_PLUGIN_ROLES), "app")
    minimum = ".".join(str(part) for part in root.ROOT_ROLE_FLOORS[role][:2])
    output = f"Load command 0\n      cmd LC_BUILD_VERSION\n  cmdsize 32\n platform 2\n    minos {minimum}\n      sdk 26.2\n"
    return subprocess.CompletedProcess(command, 0, output, "")


class RootIdentityTests(unittest.TestCase):
    def test_templates_are_exact_and_share_stays_sandboxed(self):
        root.validate_templates()
        self.assertTrue(root.expected_entitlements("app")["com.apple.private.persona-mgmt"])
        share = root.expected_entitlements("share")
        self.assertNotIn("com.apple.private.persona-mgmt", share)
        self.assertNotIn("com.apple.private.security.no-sandbox", share)
        self.assertEqual(share["com.apple.security.application-groups"], [root.ROOT_GROUP])
        self.assertEqual(share["keychain-access-groups"], [root.ROOT_ID])
        for role in ("fileprovider", "widget"):
            entitlements = root.expected_entitlements(role)
            self.assertNotIn("com.apple.private.security.no-sandbox", entitlements)
            self.assertNotIn("platform-application", entitlements)
            self.assertNotIn("com.apple.private.persona-mgmt", entitlements)
            self.assertEqual(entitlements["application-identifier"], root.ROOT_ID + root.ROOT_ROLE_SUFFIXES[role])
            self.assertEqual(entitlements["com.apple.security.application-groups"], [root.ROOT_GROUP])

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
            app = make_app(folder / "source", plugins={"MinisShare.appex": "15.0", "AgentWidgetExtension.appex": "16.2"})
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
                self.assertNotIn("Payload/Minis.app/PlugIns/AgentWidgetExtension.appex/Info.plist", ipa.namelist())
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


class RootCrossVersionTests(unittest.TestCase):
    def test_three_tiers_keep_compatible_extensions_from_one_unmodified_source(self):
        expected = {
            "15.0": {"MinisShare.appex"},
            "16.0": {"MinisShare.appex", "MinisFileProvider.appex"},
            "16.2": set(root.ROOT_PLUGIN_ROLES),
        }
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            source = make_native_app(folder / "source")
            before = {path.relative_to(source).as_posix(): path.read_bytes()
                      for path in source.rglob("*") if path.is_file()}
            helper = folder / "helper"
            helper.write_bytes(MACHO)
            for minimum, plugins in expected.items():
                with self.subTest(minimum=minimum), \
                     patch.object(root.sys, "platform", "darwin"), \
                     patch.object(root, "verify_compiled_client"), \
                     patch.object(root.shutil, "which", return_value="xcrun"), \
                     patch.object(root.subprocess, "run", side_effect=native_metadata), \
                     patch.object(root, "sign_and_verify", return_value={".": root.expected_entitlements("app")}):
                    manifest = root.package_root_app(source, helper, folder / minimum,
                                                     commit=COMMIT, run_id=RUN_ID,
                                                     minimum_os=minimum)
                self.assertEqual(manifest["minimum_os"], minimum)
                self.assertEqual(manifest["package_floor"], minimum)
                self.assertEqual(set(manifest["plugins_kept"]), plugins)
                self.assertEqual({item["name"] for item in manifest["plugins_stripped"]},
                                 set(root.ROOT_PLUGIN_ROLES) - plugins)
                self.assertTrue(manifest["archive_executable_modes_verified"])
                self.assertTrue(manifest["extension_metadata_verified"])
                self.assertFalse(manifest["device_runtime_verified"])
                self.assertFalse(manifest["root_runtime_verified"])
                for native in manifest["native_floor_audit"]:
                    role = next((role for name, role in root.ROOT_PLUGIN_ROLES.items()
                                 if name in PurePosixPath(native["path"]).parts), "app")
                    self.assertEqual(native["allowed_minimum_os"],
                                     ".".join(str(part) for part in root.ROOT_ROLE_FLOORS[role][:2]))
                with zipfile.ZipFile(folder / minimum / manifest["ipa_name"]) as archive:
                    archived = plistlib.loads(archive.read("Payload/Minis.app/Info.plist"))
                    self.assertEqual(archived["MinimumOSVersion"], minimum)
                    for name in plugins:
                        extension = plistlib.loads(archive.read(f"Payload/Minis.app/PlugIns/{name}/Info.plist"))
                        role = root.ROOT_PLUGIN_ROLES[name]
                        self.assertEqual(extension["CFBundleIdentifier"], root.ROOT_ID + root.ROOT_ROLE_SUFFIXES[role])
                        if role == "fileprovider":
                            self.assertEqual(extension["NSExtension"]["NSExtensionFileProviderDocumentGroup"], root.ROOT_GROUP)
            self.assertEqual(before, {path.relative_to(source).as_posix(): path.read_bytes()
                                      for path in source.rglob("*") if path.is_file()})

    def test_missing_compatible_extension_fails_before_signing(self):
        for minimum, plugins in (("15.0", {}),
                                 ("16.0", {"MinisShare.appex": "15.0"}),
                                 ("16.2", {"MinisShare.appex": "15.0", "MinisFileProvider.appex": "16.0"})):
            with self.subTest(minimum=minimum), tempfile.TemporaryDirectory() as temporary:
                folder = Path(temporary)
                app = make_app(folder / "source", plugins=plugins)
                helper = folder / "helper"
                helper.write_bytes(MACHO)
                with patch.object(root.sys, "platform", "darwin"), \
                     patch.object(root, "sign_and_verify") as signer:
                    with self.assertRaisesRegex(PackageError, "missing compatible extensions"):
                        root.package_root_app(app, helper, folder / "out",
                                              commit=COMMIT, run_id=RUN_ID, minimum_os=minimum)
                    signer.assert_not_called()

    def test_higher_tier_cannot_mask_core_or_extension_floor_regression(self):
        regressions = {
            "Minis": "15.1",
            "Minis.debug.dylib": "16.0",
            root.HELPER_NAME: "16.0",
            "Frameworks/Common.framework/Common": "16.0",
            "PlugIns/MinisShare.appex/MinisShare": "16.0",
            "PlugIns/MinisFileProvider.appex/MinisFileProvider": "16.1",
            "PlugIns/AgentWidgetExtension.appex/AgentWidgetExtension": "17.0",
        }
        for relative, incorrect_minimum in regressions.items():
            with self.subTest(relative=relative), tempfile.TemporaryDirectory() as temporary:
                folder = Path(temporary)
                app = make_native_app(folder / "source")
                helper = folder / "helper"
                helper.write_bytes(MACHO)

                def metadata(command, **options):
                    result = native_metadata(command, **options)
                    if command[1] == "otool" and str(command[-1]).replace("\\", "/").endswith("/" + relative):
                        result.stdout = result.stdout.replace("minos " + result.stdout.split("minos ", 1)[1].splitlines()[0],
                                                              "minos " + incorrect_minimum)
                    return result

                with patch.object(root.sys, "platform", "darwin"), \
                     patch.object(root, "verify_compiled_client"), \
                     patch.object(root.shutil, "which", return_value="xcrun"), \
                     patch.object(root.subprocess, "run", side_effect=metadata), \
                     patch.object(root, "sign_and_verify") as signer:
                    with self.assertRaisesRegex(PackageError, "native-floor audit failed"):
                        root.package_root_app(app, helper, folder / "out",
                                              commit=COMMIT, run_id=RUN_ID, minimum_os="16.2")
                    signer.assert_not_called()

    def test_only_known_extension_identity_and_floor_are_accepted(self):
        replacements = (
            {"CFBundleIdentifier": "com.openminis.app.Unrelated"},
            {"MinimumOSVersion": "15.0"},
            {"MinimumOSVersion": "16.0.1"},
            {"NSExtension": {"NSExtensionPointIdentifier": "com.apple.share-services"}},
        )
        for replacement in replacements:
            with self.subTest(replacement=replacement), tempfile.TemporaryDirectory() as temporary:
                app = make_native_app(Path(temporary))
                path = app / "PlugIns/MinisFileProvider.appex/Info.plist"
                info = load_plist(path)
                info.update(replacement)
                write_plist(path, info)
                with self.assertRaises(PackageError):
                    root.prepare_root_identity(app, COMMIT, RUN_ID, "16.2")

    def test_duplicate_and_unknown_extensions_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            app = make_native_app(Path(temporary))
            extra = app / "Extensions/MinisShare.appex"
            root.shutil.copytree(app / "PlugIns/MinisShare.appex", extra)
            with self.assertRaisesRegex(PackageError, "duplicate Root extension role"):
                root.validated_plugins(app, "16.2", complete=True)
        with tempfile.TemporaryDirectory() as temporary:
            app = make_app(Path(temporary), plugins={"Unexpected.appex": "18.0"})
            with self.assertRaisesRegex(PackageError, "unrecognized app extension"):
                root.validated_plugins(app, "15.0", complete=True)

    def test_signatures_use_role_profiles_and_frameworks_do_not_receive_extension_privileges(self):
        with tempfile.TemporaryDirectory() as temporary:
            app = make_native_app(Path(temporary))
            (app / root.HELPER_NAME).write_bytes(MACHO)
            root.prepare_root_identity(app, COMMIT, RUN_ID, "16.2")

            def entitlements(codesign, target):
                role = "app" if target == app else "helper" if target.name == root.HELPER_NAME else root.ROOT_PLUGIN_ROLES[target.name]
                return root.expected_entitlements(role)

            with patch.object(root.shutil, "which", return_value="codesign"), \
                 patch.object(root, "run_codesign") as signer, \
                 patch.object(root, "signed_entitlements", side_effect=entitlements), \
                 patch.object(root.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "", "")):
                result = root.sign_and_verify(app)
            self.assertEqual(set(result), {".", root.HELPER_NAME} | {f"PlugIns/{name}" for name in root.ROOT_PLUGIN_ROLES})
            profile_paths = {"app": root.ROOT_ENTITLEMENTS, "helper": root.HELPER_ENTITLEMENTS,
                             "share": root.SHARE_ENTITLEMENTS, "fileprovider": root.FILEPROVIDER_ENTITLEMENTS,
                             "widget": root.WIDGET_ENTITLEMENTS}
            for arguments in signer.call_args_list:
                target, extra = arguments.args[1:]
                if target == app:
                    expected = profile_paths["app"]
                elif target.name == root.HELPER_NAME:
                    expected = profile_paths["helper"]
                elif target.suffix == ".appex":
                    expected = profile_paths[root.ROOT_PLUGIN_ROLES[target.name]]
                elif target.parent.name in root.ROOT_PLUGIN_ROLES:
                    expected = profile_paths[root.ROOT_PLUGIN_ROLES[target.parent.name]]
                else:
                    self.assertEqual(extra, [], target)
                    continue
                self.assertEqual(extra, ["--entitlements", str(expected)], target)

    def test_mismatched_extension_signature_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            app = make_native_app(Path(temporary))
            (app / root.HELPER_NAME).write_bytes(MACHO)
            root.prepare_root_identity(app, COMMIT, RUN_ID, "16.2")

            def entitlements(codesign, target):
                role = "app" if target == app else "helper" if target.name == root.HELPER_NAME else root.ROOT_PLUGIN_ROLES[target.name]
                if role == "fileprovider":
                    role = "share"
                return root.expected_entitlements(role)

            with patch.object(root.shutil, "which", return_value="codesign"), \
                 patch.object(root, "run_codesign"), \
                 patch.object(root, "signed_entitlements", side_effect=entitlements), \
                 patch.object(root.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "", "")):
                with self.assertRaisesRegex(PackageError, "signed fileprovider entitlements"):
                    root.sign_and_verify(app)

    def test_unsupported_floor_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            app = make_app(Path(temporary))
            for floor in ("14.0", "16.1", "17.0", "16.2.0.extra"):
                with self.subTest(floor=floor), self.assertRaisesRegex(PackageError, "unsupported Root package floor"):
                    root.prepare_root_identity(app, COMMIT, RUN_ID, floor)


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


class RootAllExecutableArchiveTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)
        self.stage = self.folder / "stage"
        self.app = make_native_app(self.stage / "Payload")
        self.helper = self.app / root.HELPER_NAME
        self.helper.write_bytes(MACHO)
        self.info = root.prepare_root_identity(self.app, COMMIT, RUN_ID, "16.2")
        self.hashes = root.executable_hashes(self.app)
        self.plugins = {plugin.relative_to(self.app).as_posix(): load_plist(plugin / "Info.plist")
                        for plugin in root.plugin_bundles(self.app)}
        self.ipa = self.folder / "root.ipa"
        self.repack()

    def repack(self):
        root.zip_root_payload(self.stage, self.ipa)

    def verify(self):
        root.verify_root_archive(self.ipa, self.info, self.hashes[root.HELPER_NAME],
                                 expected_executables=self.hashes, expected_plugins=self.plugins)

    def rewrite_member(self, relative, *, mode=None, create_system=None, data=None,
                       duplicate=False, remove=False):
        name = "Payload/Minis.app/" + relative
        with zipfile.ZipFile(self.ipa) as archive:
            original = [(member, archive.read(member)) for member in archive.infolist()]
        with zipfile.ZipFile(self.ipa, "w") as archive:
            for member, content in original:
                if member.filename == name:
                    if remove:
                        continue
                    if mode is not None:
                        member.external_attr = mode << 16
                    if create_system is not None:
                        member.create_system = create_system
                    content = content if data is None else data
                archive.writestr(member, content)
                if duplicate and member.filename == name:
                    with self.assertWarns(UserWarning):
                        archive.writestr(member, content)

    def test_all_executables_are_regular_unix_0755_and_hashes_match(self):
        self.verify()
        self.assertGreaterEqual(len(self.hashes), 10)
        with zipfile.ZipFile(self.ipa) as archive:
            for relative, expected_hash in self.hashes.items():
                member = archive.getinfo("Payload/Minis.app/" + relative)
                self.assertEqual(member.create_system, 3)
                self.assertEqual(member.external_attr >> 16, stat.S_IFREG | 0o755)
                self.assertEqual(root.hashlib.sha256(archive.read(member)).hexdigest(), expected_hash)

    def test_any_native_executable_permission_regression_is_rejected(self):
        for relative in self.hashes:
            for mode in (stat.S_IFREG | 0o644, stat.S_IFREG | 0o4755,
                         stat.S_IFREG | 0o777, stat.S_IFLNK | 0o755):
                with self.subTest(relative=relative, mode=oct(mode)):
                    self.repack()
                    self.rewrite_member(relative, mode=mode)
                    with self.assertRaisesRegex(PackageError, "executable mode"):
                        self.verify()

    def test_non_unix_metadata_for_any_executable_is_rejected(self):
        for relative in self.hashes:
            with self.subTest(relative=relative):
                self.repack()
                self.rewrite_member(relative, create_system=0)
                with self.assertRaisesRegex(PackageError, "executable mode"):
                    self.verify()

    def test_any_replaced_executable_is_rejected_with_valid_zip_crc(self):
        for relative in self.hashes:
            with self.subTest(relative=relative):
                self.repack()
                self.rewrite_member(relative, data=MACHO + b"modified-after-signing")
                with zipfile.ZipFile(self.ipa) as archive:
                    self.assertIsNone(archive.testzip())
                with self.assertRaisesRegex(PackageError, "differs from the signed staging|differ from the signed staging"):
                    self.verify()

    def test_missing_or_duplicate_extension_executable_is_rejected(self):
        relative = "PlugIns/MinisFileProvider.appex/MinisFileProvider"
        self.rewrite_member(relative, remove=True)
        with self.assertRaisesRegex(PackageError, "missing a declared executable"):
            self.verify()
        self.repack()
        self.rewrite_member(relative, duplicate=True)
        with self.assertRaisesRegex(PackageError, "duplicate archive members"):
            self.verify()

    def test_changed_extension_metadata_is_rejected(self):
        relative = "PlugIns/MinisFileProvider.appex/Info.plist"
        info = dict(self.plugins["PlugIns/MinisFileProvider.appex"])
        info["CFBundleIdentifier"] = "com.openminis.app.FileProvider"
        self.rewrite_member(relative, data=plistlib.dumps(info))
        with self.assertRaisesRegex(PackageError, "extension metadata differs"):
            self.verify()

    def test_archive_path_escape_is_rejected(self):
        with zipfile.ZipFile(self.ipa, "a") as archive:
            archive.writestr("Payload/Minis.app/../../outside", b"bad")
        with self.assertRaisesRegex(PackageError, "invalid Root archive path"):
            self.verify()

    def test_invalid_declared_executable_path_is_rejected(self):
        info = load_plist(self.app / "PlugIns/MinisShare.appex/Info.plist")
        info["CFBundleExecutable"] = "../../Minis"
        write_plist(self.app / "PlugIns/MinisShare.appex/Info.plist", info)
        with self.assertRaisesRegex(PackageError, "invalid bundle executable name"):
            root.zip_root_payload(self.stage, self.ipa)


if __name__ == "__main__":
    unittest.main()
