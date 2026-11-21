#!/usr/bin/env python3
"""Source contracts for isolated Root storage/handoff, not device isolation proof."""
from pathlib import Path
import argparse
import re
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
IOS = ROOT / "src/ios"


def read(name):
    return (IOS / name).read_text(encoding="utf-8")


class RootIdentitySourceTests(unittest.TestCase):
    def setUp(self):
        self.shared = read("Shared/SharedContainerStore.swift")
        self.identity = read("Shared/MinisBuildIdentity.swift")

    def test_root_bundle_match_requires_a_component_boundary(self):
        self.assertIn('case .root: return "com.openminis.app.root"', self.identity)
        self.assertIn('bundleIdentifier == base || bundleIdentifier.hasPrefix(base + ".")', self.identity)
        self.assertIn('static let isRootBuild = MinisBuildIdentity.current == .root', self.shared)

    def test_broad_files_match_is_unchanged(self):
        self.assertIn('case .broadFiles: return "com.openminis.app.broadfiles"', self.identity)
        self.assertIn('static let isBroadFilesBuild = MinisBuildIdentity.current == .broadFiles', self.shared)

    def test_each_build_has_its_own_group_and_share_scheme(self):
        for property_name in ("appGroupID", "deepLinkScheme", "fileProviderDomainIdentifier"):
            self.assertIn(f'static let {property_name} = MinisBuildIdentity.current.{property_name}', self.shared)
        self.assertIn('var appGroupID: String { "group." + bundleIdentifier }', self.identity)
        self.assertIn('var fileProviderDomainIdentifier: String { bundleIdentifier + ".files" }', self.identity)
        for scheme in ("minis", "minis-root", "minis-broadfiles"):
            self.assertIn(f'return "{scheme}"', self.identity)

    def test_group_unavailable_fallback_stays_in_current_app_library(self):
        body = self.shared.split("static var storageContainerURL: URL {", 1)[1]
        body = body.split('private static let pendingShareKey', 1)[0]
        self.assertIn("forSecurityApplicationGroupIdentifier: appGroupID", body)
        self.assertIn(".libraryDirectory", body)
        self.assertIn('"MinisLocalGroup"', body)
        self.assertNotIn('"group.com.openminis.app"', body)

    def test_file_provider_visibility_uses_the_same_root_group(self):
        text = read("Views/Settings/SharedFolderVisibility.swift")
        self.assertIn('private static let appGroupID = MinisBuildIdentity.current.appGroupID', text)
        self.assertIn("UserDefaults(suiteName: appGroupID) ?? .standard", text)

    def test_main_and_extension_file_provider_consumers_share_identity(self):
        for name in ("FileProvider/FileProviderBootHealth.swift", "FileProvider/FPSyncTraceLog.swift",
                     "FileProvider/FileProviderExtension.swift"):
            with self.subTest(name=name):
                text = read(name)
                self.assertIn("MinisBuildIdentity.current.appGroupID", text)
                self.assertNotIn('forSecurityApplicationGroupIdentifier: "group.com.openminis.app"', text)
                self.assertNotIn('== "com.openminis.app.root.FileProvider"', text)
        for name in ("MinisApp.swift", "FileProvider/AppGroupChangeWatcher.swift",
                     "Views/Settings/SharedFoldersSettingsView.swift", "Views/Settings/MountDetailView.swift",
                     "Views/Rootfs/FileBrowserView.swift"):
            with self.subTest(name=name):
                text = read(name)
                self.assertIn("NSFileProviderDomainIdentifier(SharedContainerStore.fileProviderDomainIdentifier)", text)
                self.assertNotIn('NSFileProviderDomainIdentifier("com.openminis.app.files")', text)
        self.assertIn("displayName: MinisBuildIdentity.current.displayName", read("MinisApp.swift"))

    def test_identity_is_compiled_into_all_bundle_targets(self):
        project = read("Minis.xcodeproj/project.pbxproj")
        source_ids = {
            "E51000041": "52F025010000000000000020",
            "E5E000041": "52F025010000000000000021",
            "E5FP00041": "52F025010000000000000022",
            "E5H000041": "52F025010000000000000023",
        }
        for phase_id, source_id in source_ids.items():
            with self.subTest(phase=phase_id):
                match = re.search(rf"{phase_id} /\* Sources \*/ = \{{(.*?)\n\t\t\}};", project, re.S)
                self.assertIsNotNone(match)
                self.assertEqual(match.group(1).count(source_id + " /* MinisBuildIdentity.swift in Sources */"), 1)
                self.assertIn(f"{source_id} /* MinisBuildIdentity.swift in Sources */ = {{isa = PBXBuildFile; fileRef = 52F025010000000000000025", project)

    def test_share_writer_and_router_use_current_build_scheme(self):
        writer = read("ShareExtension/ShareViewController.swift")
        router = read("Shared/DeepLinkRouter.swift")
        self.assertIn('"\\(SharedContainerStore.deepLinkScheme)://share"', writer)
        self.assertRegex(
            router,
            r'guard url\.scheme == SharedContainerStore\.deepLinkScheme\s*'
            r'\|\| \(!SharedContainerStore\.isRootBuild && url\.scheme == "minis"\),')
        self.assertNotIn("isBroadFilesBuild &&", router)

    def test_main_storage_consumers_keep_central_group_resolution(self):
        for name in ("MinisApp.swift", "Debug/DebugJSONRPC.swift",
                     "Agent/Chat/AIChatViewModel+RequestBudget.swift"):
            with self.subTest(name=name):
                text = read(name)
                self.assertIn("SharedContainerStore.", text)
                self.assertNotIn('forSecurityApplicationGroupIdentifier: "group.com.openminis.app"', text)


def native_identity_test():
    if sys.platform != "darwin":
        raise SystemExit("Native identity tests run on GitHub Actions macOS only.")
    swiftc = shutil.which("swiftc")
    if swiftc is None:
        raise SystemExit("swiftc is required for native identity tests.")
    with tempfile.TemporaryDirectory(prefix="minis-identity-") as temporary:
        binary = Path(temporary) / "identity-tests"
        subprocess.run([swiftc, "-swift-version", "5", "-parse-as-library", "-warnings-as-errors",
                        str(IOS / "Shared/MinisBuildIdentity.swift"),
                        str(ROOT / "tests/MinisBuildIdentityTests.swift"), "-o", str(binary)], check=True)
        subprocess.run([str(binary)], check=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--native", action="store_true")
    arguments, unittest_arguments = parser.parse_known_args()
    result = unittest.main(argv=[sys.argv[0], *unittest_arguments], verbosity=2, exit=False)
    if not result.result.wasSuccessful():
        raise SystemExit(1)
    if arguments.native:
        native_identity_test()
