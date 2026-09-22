#!/usr/bin/env python3
"""Contract tests for the iOS 15 legacy-host measurement readiness gate."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
LEGACY = ROOT / "src/ios/Shared/LegacyHostingContent.swift"
MARKDOWN = ROOT / "src/ios/Views/Chat/SelectableMarkdownView.swift"


class LegacyMeasurementReadinessTests(unittest.TestCase):
    def test_legacy_host_has_an_explicit_readiness_contract(self):
        source = LEGACY.read_text()
        self.assertIn("protocol LegacyHostedMeasurementReadiness", source)
        self.assertIn("func hostedMeasurementIsReady() -> Bool", source)
        self.assertIn("allSatisfy { $0.legacyHostedMeasurementReady }", source)
        self.assertIn("payload.revision == measurementPreferenceRevision", source)
        self.assertIn("measurementPreferenceRevision &+= 1", source)
        self.assertIn("measurementRevision.value = revision", source)
        self.assertIn("revision: measurementRevision", source)

    def test_unready_payload_is_rejected_before_last_size_or_pending_state(self):
        source = LEGACY.read_text()
        start = source.index("    private func contentSizeChanged(")
        end = source.index("\n    deinit", start)
        method = source[start:end]
        self.assertIn("guard hostedMeasurementIsReady() else { return }", method)
        self.assertLess(method.index("guard hostedMeasurementIsReady()"), method.index("lastSize = size"))
        self.assertLess(method.index("guard hostedMeasurementIsReady()"), method.index("pendingSize = size"))
        self.assertIn("let revision = payload.revision", method)
        self.assertIn("self.measurementPreferenceRevision == revision", method)

    def test_markdown_view_reports_finite_bounds_and_container_width(self):
        source = MARKDOWN.read_text()
        start = source.index("final class SelectableMarkdownTextView:")
        end = source.index("\n// MARK: - SelectableMarkdownView (UIViewRepresentable)", start)
        declaration = source[start:end]
        self.assertIn("LegacyHostedMeasurementReadiness", declaration)
        self.assertIn("legacyHostedMeasurementReady", declaration)
        self.assertIn("bounds.width > 1", declaration)
        self.assertIn("textContainer.size.width > 1", declaration)


if __name__ == "__main__":
    unittest.main(verbosity=2)
