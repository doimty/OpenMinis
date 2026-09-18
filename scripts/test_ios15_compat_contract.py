#!/usr/bin/env python3
"""Narrow structural guards, not a substitute for the Apple-compiler smoke."""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COMPAT = ROOT / "src/ios/Shared/SwiftUICompatibility.swift"


class CompatibilityContractTests(unittest.TestCase):
    def test_range_line_limit_adapter_has_one_owner(self):
        source = COMPAT.read_text()
        definitions = re.findall(r"\bfunc\s+compatLineLimit\s*\(", source)
        self.assertEqual(len(definitions), 1, "duplicate Swift declarations are not syntax-tree errors")

    def test_visibility_signature_does_not_leak_newer_sdk_type(self):
        source = COMPAT.read_text()
        signatures = re.findall(r"func\s+compat\w+[^\{]+\{", source)
        self.assertFalse(any("ToolbarPlacement" in signature for signature in signatures),
                         "a body availability guard does not protect the signature")

    def test_legacy_vertical_editor_is_not_a_single_line_text_field(self):
        source = COMPAT.read_text()
        self.assertIn("TextEditor(text:", source)
        self.assertNotIn("func compatTextFieldAxis", source,
                         "retire the helper that silently drops the vertical axis")


if __name__ == "__main__":
    unittest.main()
