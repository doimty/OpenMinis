#!/usr/bin/env python3
"""Source contracts for isolated Root storage/handoff, not device isolation proof."""
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
IOS = ROOT / "src/ios"


def read(name):
    return (IOS / name).read_text(encoding="utf-8")


class RootIdentitySourceTests(unittest.TestCase):
    def setUp(self):
        self.shared = read("Shared/SharedContainerStore.swift")

    def flag_body(self, name):
        match = re.search(rf"static let {name}: Bool = \{{(.*?)\}}\(\)",
                          self.shared, re.S)
        self.assertIsNotNone(match, name)
        return match.group(1)

    def test_root_bundle_match_requires_a_component_boundary(self):
        body = self.flag_body("isRootBuild")
        self.assertIn('let id = Bundle.main.bundleIdentifier ?? ""', body)
        self.assertIn('let base = "com.openminis.app.root"', body)
        # Covers the main app and extensions without accepting .rooted/.root2.
        self.assertRegex(body, r'return id == base \|\| id\.hasPrefix\(base \+ "\."\)\s*$')

    def test_broad_files_match_is_unchanged(self):
        body = self.flag_body("isBroadFilesBuild")
        self.assertIn('let base = "com.openminis.app.broadfiles"', body)
        self.assertRegex(body, r'return id == base \|\| id\.hasPrefix\(base \+ "\."\)\s*$')
        self.assertNotIn("isRootBuild", body)

    def test_each_build_has_its_own_group_and_share_scheme(self):
        self.assertRegex(
            self.shared,
            r'static let appGroupID = isRootBuild\s*\? "group\.com\.openminis\.app\.root"'
            r'\s*: \(isBroadFilesBuild \? "group\.com\.openminis\.app\.broadfiles"'
            r' : "group\.com\.openminis\.app"\)')
        self.assertRegex(
            self.shared,
            r'static let deepLinkScheme = isRootBuild\s*\? "minis-root"'
            r' : \(isBroadFilesBuild \? "minis-broadfiles" : "minis"\)')

    def test_group_unavailable_fallback_stays_in_current_app_library(self):
        body = self.shared.split("static var storageContainerURL: URL {", 1)[1]
        body = body.split('private static let pendingShareKey', 1)[0]
        self.assertIn("forSecurityApplicationGroupIdentifier: appGroupID", body)
        self.assertIn(".libraryDirectory", body)
        self.assertIn('"MinisLocalGroup"', body)
        self.assertNotIn('"group.com.openminis.app"', body)

    def test_file_provider_visibility_uses_the_same_root_group(self):
        text = read("Views/Settings/SharedFolderVisibility.swift")
        self.assertIn('let root = "com.openminis.app.root"', text)
        self.assertRegex(
            text,
            r'if id == root \|\| id\.hasPrefix\(root \+ "\."\) \{\s*'
            r'return "group\.com\.openminis\.app\.root"\s*\}')
        self.assertIn('let base = "com.openminis.app.broadfiles"', text)
        self.assertIn('id == base || id.hasPrefix(base + ".")', text)
        self.assertIn("UserDefaults(suiteName: appGroupID) ?? .standard", text)

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


if __name__ == "__main__":
    unittest.main(verbosity=2)
