#!/usr/bin/env python3
"""Contract checks for the source-derived session-width probe."""
from pathlib import Path
import json
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from prepare_probe import extract

ROOT = Path(__file__).resolve().parents[2]
PROBE = Path(__file__).with_name("ProbeApp.swift")


class ProbeInputTests(unittest.TestCase):
    def test_extraction_is_outside_production_and_contains_real_attachment(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            manifest = extract(ROOT, output)
            generated = (output / "CodeBlockAttachment.swift").read_text()
            self.assertEqual(manifest["commit"], __import__("subprocess").check_output(
                ["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True).strip())
            self.assertIn("final class CodeBlockAttachment: NSTextAttachment", generated)
            self.assertIn("override func attachmentBounds", generated)
            self.assertIn("func makeView(width: CGFloat)", generated)
            self.assertTrue((output / "LegacyHostingContent.swift").is_file())
            self.assertFalse((ROOT / "src" / "CodeBlockAttachment.swift").exists())

    def test_probe_is_neutral_and_has_both_contracts(self):
        source = PROBE.read_text()
        self.assertIn("case currentFallback", source)
        self.assertIn("case noWidthDemandFallback", source)
        self.assertIn("candidate-suppresses-invalid-width-demand", source)
        for observed_height in ("1521", "1354", "1865", "1484"):
            self.assertNotIn(observed_height, source)
        self.assertIn("five-code-block", source)
        self.assertIn("not private conversation text", source)
        self.assertIn("--probe-run-id=", source)

    def test_manifest_declares_limits(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest = extract(ROOT, Path(tmp))
            self.assertEqual(manifest["fixture"], "neutral five-code-block structural fixture; no original conversation text included")
            self.assertEqual(len(manifest["limits"]), 3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
