#!/usr/bin/env python3
"""Check Root-only automation wiring; compile the production state only on Actions macOS."""
from pathlib import Path
import argparse
import json
import re
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
IOS = ROOT / "src/ios"
SOURCE = IOS / "Diagnostics/AutomationMCPController.swift"


class AutomationSourceTests(unittest.TestCase):
    def test_only_root_main_target_contains_automation_sources(self):
        source = SOURCE.read_text(encoding="utf-8")
        self.assertTrue(source.startswith("#if MINIS_ROOT_MANAGEMENT\n"))
        self.assertIn("#if os(iOS)", source)
        project = (IOS / "Minis.xcodeproj/project.pbxproj").read_text(encoding="utf-8")
        main = project.split("E51000041 /* Sources */ = {", 1)[1].split("runOnlyForDeploymentPostprocessing", 1)[0]
        for name in ("AutomationMCPController.swift", "AutomationMCPOffload.m"):
            self.assertIn(name + " in Sources", main)
            self.assertEqual(project.count(name + " in Sources"), 2)
        kernel = (IOS / "iSH/ISHKernel.m").read_text(encoding="utf-8")
        registration = kernel.split("    device_mcp_offload_register();", 1)[1].split("#endif", 1)[0]
        self.assertIn("automation_mcp_offload_register();", registration)

    def test_transport_has_no_guest_grant_or_persisted_user_configuration(self):
        offload = (IOS / "NativeOffloads/AutomationMCPOffload.m").read_text(encoding="utf-8")
        self.assertTrue(offload.startswith("#if defined(MINIS_ROOT_MANAGEMENT) && MINIS_ROOT_MANAGEMENT"))
        self.assertLess(
            offload.index("#import <UIKit/UIKit.h>"),
            offload.index('#import "Minis-Swift.h"'),
        )
        self.assertIn("if (argc != 1) return NOFF_EXIT_INVALID_ARGS", offload)
        self.assertIn("[AutomationMCPBridge transport]", offload)
        self.assertNotIn("requestStart", offload)
        store = (IOS / "Agent/Session/MCPStore.swift").read_text(encoding="utf-8")
        self.assertIn('reservedAutomationServerName = "minis-automation"', store)
        self.assertIn("name.caseInsensitiveCompare(reservedAutomationServerName)", store)
        self.assertIn("result.removeAll { Self.isReservedServerName($0.id) }", store)
        self.assertIn("parsed.map(\\.id).filter { !Self.isReservedServerName($0) }", store)

    def test_backgrounding_does_not_implicitly_start_or_stop_cross_app_grants(self):
        source = SOURCE.read_text(encoding="utf-8")
        for notification in ("willResignActiveNotification", "didEnterBackgroundNotification", "willDeactivateNotification"):
            self.assertNotIn(notification, source)
        self.assertIn("mach_continuous_time()", source)
        self.assertIn("SecRandomCopyBytes", source)
        self.assertIn("static let duration: Double = 180", source)
        self.assertNotIn("UserDefaults", source)
        self.assertNotIn("Logger", source)
        self.assertNotIn("print(", source)

    def test_new_settings_and_confirmation_are_translated(self):
        catalog = json.loads((IOS / "Localizable.xcstrings").read_text(encoding="utf-8"))["strings"]
        view = (IOS / "Views/MCP/MCPIntegrationsView.swift").read_text(encoding="utf-8")
        section = view.split("private struct AutomationMCPSettingsSection", 1)[1].split("private struct DeviceMCPSettingsSection", 1)[0]
        keys = set(re.findall(r'AppLocalized\("([^"\\]*)"\)', section + SOURCE.read_text(encoding="utf-8")))
        keys.add("The automation session could not be created securely. Check the companion installation and try again.")
        self.assertGreaterEqual(len(keys), 10)
        for key in keys:
            for locale in ("en", "zh-Hans", "zh-Hant"):
                with self.subTest(key=key, locale=locale):
                    unit = catalog[key]["localizations"][locale]["stringUnit"]
                    self.assertEqual(unit["state"], "translated")
                    self.assertTrue(unit["value"].strip())


def native():
    with tempfile.TemporaryDirectory(prefix="minis-automation-native-") as temporary:
        directory = Path(temporary)
        binary = directory / "automation-tests"
        subprocess.run(["xcrun", "swiftc", "-swift-version", "5", "-warnings-as-errors", "-parse-as-library",
                        "-D", "MINIS_ROOT_MANAGEMENT", str(SOURCE), str(ROOT / "tests/AutomationSessionTests.swift"),
                        "-o", str(binary)], check=True, timeout=120)
        subprocess.run([str(binary)], check=True, timeout=20)
        sdk = subprocess.check_output(["xcrun", "--sdk", "iphoneos", "--show-sdk-path"], text=True).strip()
        bundle = directory / "LocalizationBundle.swift"
        bundle.write_text("import Foundation\nextension Bundle { var languageBundle: Bundle? { nil } }\n", encoding="utf-8")
        header = IOS / "Diagnostics/RootManagementClient.h"
        subprocess.run(["xcrun", "--sdk", "iphoneos", "swiftc", "-typecheck", "-parse-as-library", "-swift-version", "5",
                        "-warnings-as-errors", "-target", "arm64-apple-ios15.0", "-sdk", sdk,
                        "-D", "MINIS_ROOT_MANAGEMENT", "-Xcc", "-DMINIS_ROOT_MANAGEMENT=1",
                        "-import-objc-header", str(header), str(IOS / "Diagnostics/RootManagementAuthorization.swift"),
                        str(IOS / "Diagnostics/RootManagementRunner.swift"), str(IOS / "Shared/AppLocalization.swift"),
                        str(bundle), str(SOURCE)], check=True, timeout=180)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--native", action="store_true")
    arguments, test_arguments = parser.parse_known_args()
    result = unittest.main(argv=[__file__, *test_arguments], verbosity=2, exit=False)
    if not result.result.wasSuccessful():
        raise SystemExit(1)
    if arguments.native:
        native()
