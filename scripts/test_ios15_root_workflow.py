#!/usr/bin/env python3
"""Static checks for the cross-version Root/standard Actions workflow."""
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/ios15-m0-baseline.yml"


class WorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = WORKFLOW.read_text(encoding="utf-8")

    def test_root_native_gates_include_device_mcp_and_identity(self):
        self.assertIn("python3 scripts/test_device_mcp_native.py --native", self.source)
        self.assertIn("python3 scripts/test_automation_mcp_native.py --native", self.source)
        self.assertIn("python3 scripts/test_ios15_root_identity.py --native", self.source)
        self.assertIn("python3 scripts/test_device_mcp_stdio.py -v", self.source)
        root_build = self.source.split("- name: Build isolated Minis Root and bundled helper", 1)[1]
        root_build = root_build.split("\n      - name:", 1)[0]
        self.assertIn("DeviceMCPOffload.m", root_build)
        self.assertIn("AutomationMCPOffload.m", root_build)
        self.assertIn("-fsyntax-only", root_build)
        self.assertIn("Minis-Swift.h", root_build)

    def test_three_root_floors_and_requested_jailbreak_schemes_are_packaged(self):
        self.assertIn('--minimum-os "15.0"', self.source)
        self.assertIn('for floor in 16.0 16.2', self.source)
        for scheme in ("rootless", "roothide"):
            self.assertIn(f'--scheme "$scheme"', self.source)
        self.assertIn("for scheme in rootless roothide; do", self.source)
        self.assertNotIn("for scheme in rootful rootless roothide; do", self.source)
        self.assertIn("scripts/package_jailbreak_deb.py", self.source)
        self.assertIn("deb-*/manifest.json", self.source)

    def test_standard_release_is_built_without_root_flags_and_audited(self):
        block = self.source.split("- name: Build and audit standard Release product", 1)[1]
        block = block.split("\n      - name:", 1)[0]
        self.assertIn("-configuration Release", block)
        self.assertIn("DerivedData", block)
        self.assertNotIn("MINIS_ROOT_MANAGEMENT", block)
        self.assertNotIn("MINIS_HOST_FILE_PROBE", block)
        self.assertIn("scripts/audit_standard_ios_build.py", block)
        self.assertIn("--build-log", block)

    def test_fixture_and_report_verifiers_run_on_linux(self):
        for command in (
            "python3 scripts/test_package_jailbreak_deb.py -v",
            "python3 scripts/test_verify_jailbreak_device_report.py -v",
            "python3 scripts/test_audit_standard_ios_build.py -v",
            "python3 scripts/test_package_automation_deb.py -v",
        ):
            self.assertIn(command, self.source)

    def test_companion_is_built_for_requested_schemes_without_installation(self):
        block = self.source.split("  automation-companion:", 1)[1].split("\n  baseline:", 1)[0]
        self.assertIn("scheme: [rootless, roothide]", block)
        self.assertIn("jailbreak/automation/build.sh", block)
        self.assertIn("jailbreak/automation/test_native.py --native", block)
        self.assertIn("scripts/package_automation_deb.py", block)
        self.assertIn("minis-automation-${{ matrix.scheme }}", block)
        self.assertNotIn("killall", block)
        self.assertNotIn("dpkg -i", block)


if __name__ == "__main__":
    unittest.main(verbosity=2)
