#!/usr/bin/env python3
"""Structural gate: no production LegacyHostingContentView may leave hosting
content hit-testable outside its own bounds. The runtime reporting probe is
separate (scripts/run_ios15_retry_probe.sh)."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parent.parent


class RetryHitContractTests(unittest.TestCase):
    def test_legacy_probe_and_runner_exist(self):
        probe = ROOT / "scripts/ios15-retry-probe/ProbeApp.swift"
        self.assertTrue(probe.exists())
        self.assertTrue((ROOT / "scripts/run_ios15_retry_probe.sh").exists())
        self.assertIn("LegacyHostingConfiguration", probe.read_text())

    def test_workflow_has_retry_probe_step(self):
        workflow = (ROOT / ".github/workflows/ios15-m0-baseline.yml").read_text()
        self.assertIn("run_ios15_retry_probe.sh", workflow)

    def test_bottom_inset_floors_unreported_floating_preview(self):
        source = (ROOT / "src/ios/Agent/MessageList/CollectionViewMessageListV3.swift").read_text()
        self.assertIn("effectiveFloatingHeight", source)
        self.assertIn("floatingBarHeight > 1", source)
        self.assertIn("hasToolBlocks ? 100 : 0", source)
        self.assertIn("223.67 inset = 115.67 inputBar", source, "floor must match measured device preview height")

    def test_legacy_host_keeps_bottom_pin_free_but_extends_hit_testing(self):
        # The host view may overflow a short cell estimate; the content view
        # must never leave that overflow touch-dead. The production fix is a
        # bounds-aware hitTest, not a bottom constraint (which would change
        # intrinsic sizing).
        source = (ROOT / "src/ios/Shared/LegacyHostingContent.swift").read_text()
        self.assertNotIn("host.view.bottomAnchor.constraint", source)
        self.assertIn("override func hitTest", source)
        self.assertIn("host.view", source.split("override func hitTest", 1)[1][:900])


if __name__ == "__main__":
    unittest.main(verbosity=2) if "--audit" not in sys.argv else unittest.TextTestRunner(verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromName("RetryHitContractTests"))