#!/usr/bin/env python3
"""Fixture provenance/shape checks, NOT native rendering acceptance."""
import tempfile
import unittest
from pathlib import Path
from prepare_ios15_composer_probe import prepare, between

ROOT = Path(__file__).resolve().parent.parent


class ComposerProbeContracts(unittest.TestCase):
    def test_production_extraction_does_not_reimplement_cell_or_parent(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            manifest = prepare(ROOT, out)
            original = (ROOT / "src/ios/Agent/MessageList/MessageListInfrastructure.swift").read_text()
            extracted = (out / "ProductionInfrastructure.swift").read_text()
            restored = extracted.replace("if #available(iOS 16.0, *), !ProbeSettings.forceLegacy {",
                                         "if #available(iOS 16.0, *) {")
            self.assertEqual(restored, original.split("// MARK: - Cell State Bridge", 1)[0])
            self.assertIn("final class MessageListViewController", extracted)
            self.assertIn("final class NoAnimationCollectionView", extracted)
            self.assertIn("self.legacyMeasuredSize = size", extracted)
            source = (ROOT / "src/ios/Views/Chat/AIChatView.swift").read_text()
            section = between(source, "                // Attachment preview chips — 3 per row",
                              "                inputFieldOrWaveform")
            self.assertIn(section, (out / "ComposerFixture.swift").read_text())
            self.assertEqual(len(manifest["source_sha256"]), 7)
            fixture = (out / "ComposerFixture.swift").read_text()
            self.assertIn(".modifier(ComposerSurface())", fixture)
            self.assertIn(".compatOnGeometryChange(for: CGRect.self)", fixture)
            self.assertIn("content.onGeometryChange(for: CGRect.self", fixture)
            self.assertIn("recordFrame(name, frame)", fixture)
            self.assertIn(".onDisappear", fixture)
            self.assertIn("store?.frames[name] = nil", fixture)
            self.assertNotIn("ProbeFramesKey", fixture)
            self.assertIn("LegacyGeometryObserver", (out / "ProductionGeometry.swift").read_text())

    def test_runtime_observes_frames_not_manual_layout_writes(self):
        s = (ROOT / "scripts/ios15-composer-probe/ProbeApp.swift").read_text()
        self.assertIn("let vc = MessageListViewController()", s)
        self.assertIn("cell.applyHostedContent(parent: vc)", s)
        self.assertIn("host.convert(host.bounds, to: cv).maxY", s)
        self.assertNotIn("setCachedHeight(", s)
        self.assertNotIn("seedMeasuredHeight(", s)
        self.assertNotIn("cell.preferredLayoutAttributesFitting", s)
        self.assertIn("sampleCollection(\"collection-grow\"", s)
        self.assertIn("sampleComposer(\"baseline-readd\"", s)
        self.assertIn("chips.allSatisfy", s)
        self.assertIn("$0.maxY <= field.minY", s)

    def test_extraction_rejects_ambiguous_start(self):
        with self.assertRaises(ValueError):
            between("START START END", "START", "END")


if __name__ == "__main__":
    unittest.main()
