#!/usr/bin/env python3
"""Regression contract for the iOS 15 legacy Markdown width fallback."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src/ios/Views/Chat/SelectableMarkdownView.swift"


class SessionWidthContractTests(unittest.TestCase):
    def test_invalid_bounds_do_not_report_textview_ideal_width(self):
        source = SOURCE.read_text()
        marker = "    override var intrinsicContentSize: CGSize {"
        self.assertEqual(source.count(marker), 1)
        method = source[source.index(marker):source.index("\n    // [T-ios-table-cell-image-menu]", source.index(marker))]
        self.assertIn("guard bounds.width > 1, bounds.width.isFinite else", method)
        self.assertIn(
            "return CGSize(width: UIView.noIntrinsicMetric, height: original.height)",
            method,
        )
        self.assertNotIn("return original", method)


if __name__ == "__main__":
    unittest.main(verbosity=2)
