#!/usr/bin/env python3
"""Packaging/policy tests, not iOS runtime entitlement or UI evidence."""
import json
import plistlib
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import package_ios15_ipa as p
from test_package_ios15_ipa import make_app

ROOT = Path(__file__).resolve().parents[1]


class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.app = make_app(self.root, plugins={"MinisShare.appex": "15.0"})
        self.out = self.root / "out"

    def package(self, **kw):
        return p.package_app(self.app, self.out, skip_sign=True, **kw)

    def override(self, values):
        path = self.root / "override.plist"
        path.write_bytes(plistlib.dumps(values))
        return path

    def test_default_stays_ordinary(self):
        args = p.build_parser().parse_args(["--app", str(self.app), "--output-dir", str(self.out)])
        self.assertEqual(args.file_access_profile, "standard")
        manifest = self.package()
        self.assertEqual(manifest["file_access_profile"], "standard")
        self.assertEqual(manifest["bundle_id"], p.NORMAL_BUNDLE_ID)
        self.assertNotIn("no-sandbox", str(manifest["requested_entitlements"]))
        self.assertNotIn("broadfiles", manifest["ipa_name"])

    def test_broad_identity_and_source_are_isolated(self):
        before = (self.app / "Info.plist").read_bytes()
        manifest = self.package(file_access_profile="broad-files")
        self.assertEqual((self.app / "Info.plist").read_bytes(), before)
        staged = self.out / "stage/Payload/Minis.app"
        info = p.load_plist(staged / "Info.plist")
        self.assertEqual(info["CFBundleIdentifier"], p.BROAD_BUNDLE_ID)
        self.assertEqual(info["CFBundleDisplayName"], "Minis Files")
        self.assertEqual(info["CFBundleURLTypes"][0]["CFBundleURLSchemes"], ["minis-broadfiles"])
        self.assertIn("broadfiles", manifest["ipa_name"])
        self.assertFalse(manifest["signed"])
        self.assertFalse(manifest["signature_entitlements_verified"])
        self.assertFalse(manifest["runtime_file_access_verified"])
        self.assertFalse(manifest["root_helper_included"])

    def test_share_is_not_unsandboxed(self):
        plugin = self.app / "PlugIns/MinisShare.appex"
        before = (plugin / "Info.plist").read_bytes()
        self.package(file_access_profile="broad-files")
        staged = self.out / "stage/Payload/Minis.app/PlugIns/MinisShare.appex"
        self.assertEqual(p.load_plist(staged / "Info.plist")["CFBundleIdentifier"],
                         p.BROAD_BUNDLE_ID + ".ShareExtension")
        self.assertEqual((plugin / "Info.plist").read_bytes(), before)
        values = p.load_plist(p.BROAD_SHARE_ENTITLEMENTS)
        self.assertEqual(values["com.apple.security.application-groups"], [p.BROAD_APP_GROUP])
        self.assertNotIn("com.apple.private.security.no-sandbox", values)

    def test_unknown_profile_is_rejected(self):
        with self.assertRaises(p.PackageError):
            self.package(file_access_profile="root")
        self.assertFalse(self.out.exists())

    def test_default_cannot_take_broad_entitlements(self):
        with self.assertRaises(p.PackageError):
            self.package(entitlements=p.BROAD_FILES_ENTITLEMENTS)

    def test_no_privilege_extras_even_false_booleans(self):
        for key in ("com.apple.private.persona-mgmt", "com.apple.private.tcc.allow",
                    "task_for_pid-allow", "com.apple.private.cs.debugger",
                    "dynamic-codesigning", "run-unsigned-code"):
            for value in (True, False):
                with self.subTest(key=key, value=value):
                    values = p.expected_broad_entitlements()
                    values[key] = value
                    with self.assertRaises(p.PackageError):
                        self.package(file_access_profile="broad-files",
                                     entitlements=self.override(values))

    def test_integer_cannot_impersonate_boolean_entitlement(self):
        values = p.expected_broad_entitlements()
        values["platform-application"] = 1
        with self.assertRaises(p.PackageError):
            self.package(file_access_profile="broad-files", entitlements=self.override(values))
        self.assertFalse(p.entitlement_values_equal({"x": 1}, {"x": True}))

    def test_profiles_cannot_share_output_directory(self):
        self.package()
        with self.assertRaises(p.PackageError):
            self.package(file_access_profile="broad-files")
        self.assertEqual(json.loads((self.out / "manifest.json").read_text())["file_access_profile"], "standard")

    def test_broad_refuses_diagnostics_or_incompatible_plugins(self):
        for options in ({"diagnostic_commit": "a" * 40}, {"keep_incompatible_plugins": True}):
            with self.subTest(options=options), self.assertRaises(p.PackageError):
                self.package(file_access_profile="broad-files", **options)

    def test_broad_source_cannot_be_labeled_ordinary(self):
        info = p.load_plist(self.app / "Info.plist")
        info["CFBundleIdentifier"] = p.BROAD_BUNDLE_ID
        p.write_bundle_info(self.app, info)
        with self.assertRaises(p.PackageError):
            self.package()

    def test_share_profile_cannot_inherit_native_unsandboxing(self):
        values = p.expected_broad_entitlements(share=True)
        values["com.apple.private.security.no-sandbox"] = True
        with mock.patch.object(p, "BROAD_SHARE_ENTITLEMENTS", self.override(values)):
            with self.assertRaises(p.PackageError):
                self.package(file_access_profile="broad-files")

    def test_signed_profile_has_actual_readback_hook(self):
        with mock.patch.object(p, "audit_staged_native_floor", return_value={}), \
             mock.patch.object(p, "adhoc_sign") as sign, \
             mock.patch.object(p, "verify_broad_files_signatures", return_value={"app": p.expected_broad_entitlements()}) as verify:
            manifest = p.package_app(self.app, self.out, file_access_profile="broad-files")
        self.assertTrue(manifest["signed"])
        self.assertTrue(manifest["signature_entitlements_verified"])
        self.assertFalse(manifest["runtime_file_access_verified"])
        self.assertEqual(sign.call_args.kwargs["share_entitlements"], p.BROAD_SHARE_ENTITLEMENTS)
        verify.assert_called_once()

    def test_signature_mismatch_fails_before_delivering_ipa(self):
        with mock.patch.object(p, "audit_staged_native_floor", return_value={}), \
             mock.patch.object(p, "adhoc_sign"), \
             mock.patch.object(p, "verify_broad_files_signatures", side_effect=p.PackageError("mismatch")):
            with self.assertRaises(p.PackageError):
                p.package_app(self.app, self.out, file_access_profile="broad-files")
        self.assertFalse((self.out / "manifest.json").exists())
        self.assertEqual(list(self.out.glob("*.ipa")), [])

    def test_codesign_plist_parser_and_missing_entitlements(self):
        values = p.expected_broad_entitlements()
        good = mock.Mock(returncode=0, stdout=plistlib.dumps(values), stderr=b"signature info\n")
        with mock.patch.object(p.subprocess, "run", return_value=good):
            self.assertEqual(p.signed_entitlements("codesign", self.app), values)
        bad = mock.Mock(returncode=0, stdout=b"", stderr=b"no entitlements")
        with mock.patch.object(p.subprocess, "run", return_value=bad):
            with self.assertRaises(p.PackageError):
                p.signed_entitlements("codesign", self.app)

    def test_source_uses_current_identity_for_shared_stores(self):
        base = ROOT / "src/ios"
        shared = (base / "Shared/SharedContainerStore.swift").read_text(encoding="utf-8")
        identity = (base / "Shared/MinisBuildIdentity.swift").read_text(encoding="utf-8")
        self.assertIn('bundleIdentifier == base || bundleIdentifier.hasPrefix(base + ".")', identity)
        self.assertIn('case .broadFiles: return "com.openminis.app.broadfiles"', identity)
        self.assertIn("MinisBuildIdentity.current.appGroupID", shared)
        for name in ("MinisApp.swift", "Debug/DebugJSONRPC.swift"):
            text = (base / name).read_text(encoding="utf-8")
            self.assertNotIn('forSecurityApplicationGroupIdentifier: "group.com.openminis.app"', text)
            self.assertIn("SharedContainerStore.appGroupID", text)
        share = (base / "ShareExtension/ShareViewController.swift").read_text(encoding="utf-8")
        self.assertIn("SharedContainerStore.deepLinkScheme", share)


if __name__ == "__main__":
    unittest.main(verbosity=2)
