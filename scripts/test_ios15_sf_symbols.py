#!/usr/bin/env python3
"""Parser/coverage regressions; Swift runtime tests exercise the real resolver."""
import json
import unittest

from tree_sitter import Language, Parser
import tree_sitter_swift

from audit_ios15_sf_symbols import CATALOG, ROOT, audit, exact_edits, unsafe_calls


class SymbolAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = json.loads(CATALOG.read_text())["introduced_ios"]
        cls.parser = Parser(Language(tree_sitter_swift.language()))

    def calls(self, source):
        return unsafe_calls(source.encode(), self.catalog, self.parser)

    def test_device_voice_template_pattern_is_red_without_resolver(self):
        self.assertEqual(len(self.calls("Image(systemName: template.symbol)")), 1)
        self.assertEqual(len(self.calls('Image(systemName: "mic.and.signal.meter")')), 1)

    def test_supported_symbols_and_safe_toggle_stay_native(self):
        for source in ['Image(systemName: "mic")',
                       'Image(systemName: playing ? "pause.fill" : "play.fill")',
                       'Label("Copy", systemImage: "doc.on.doc")']:
            with self.subTest(source=source):
                self.assertEqual(self.calls(source), [])

    def test_one_late_toggle_branch_requires_resolver(self):
        self.assertEqual(len(self.calls('Image(systemName: key ? "key" : "person.badge.shield.checkmark")')), 1)

    def test_unknown_empty_and_dynamic_names_are_protected(self):
        for expr in ['"not.a.real.symbol"', '""', 'model.icon', 'icon(for: model)']:
            self.assertEqual(len(self.calls(f"Image(systemName: {expr})")), 1)

    def test_uikit_configuration_and_label_are_covered(self):
        self.assertEqual(len(self.calls('UIImage(systemName: name, withConfiguration: config)')), 1)
        self.assertEqual(len(self.calls('Label(title, systemImage: name)')), 1)

    def test_comments_strings_and_declarations_are_not_renderers(self):
        source = '''// Image(systemName: wrong)
let sample = """
UIImage(systemName: item.imageName)
"""
var systemImage: String { "mic.and.signal.meter" }
func show(systemImage: String) {}
VoiceTemplate(symbol: "mic.and.signal.meter")
'''
        self.assertEqual(self.calls(source), [])

    def test_exact_edits_preserve_non_symbol_arguments_and_are_idempotent(self):
        source = '''Label("Literal key", systemImage: icon)
Label(localizedTitle, systemImage: icon)
UIImage(systemName: icon, withConfiguration: config)
Image(systemName: icon)
Image(systemName: icon)
'''
        data = source.encode()
        edits = exact_edits(data, unsafe_calls(data, self.catalog, self.parser))
        text = source
        for edit in edits:
            self.assertEqual(source.count(edit["oldText"]), 1)
            text = text.replace(edit["oldText"], edit["newText"])
        self.assertEqual(self.calls(text), [])
        self.assertIn('Label("Literal key", systemImage: CompatSystemSymbol.name(icon))', text)
        self.assertIn('Label(localizedTitle, systemImage: CompatSystemSymbol.name(icon))', text)
        self.assertIn('UIImage(systemName: CompatSystemSymbol.name(icon), withConfiguration: config)', text)

    def test_catalog_pin_and_known_introductions(self):
        meta = json.loads(CATALOG.read_text())
        self.assertEqual(meta["source_commit"], "cb2e670a213ff42ae08528ee2c401bfb1d799675")
        self.assertEqual(self.catalog["mic.and.signal.meter"], "16.0")
        self.assertEqual(self.catalog["waveform.badge.mic"], "17.0")
        self.assertEqual(self.catalog["arrow.trianglehead.2.counterclockwise"], "18.0")

    def test_owner_is_registered_and_native_ci_gate_is_wired(self):
        project = (ROOT / "src/ios/Minis.xcodeproj/project.pbxproj").read_text()
        self.assertEqual(project.count("15C01500000000000000000F /* CompatSystemSymbol.swift in Sources */"), 2)
        self.assertEqual(project.count("15C015000000000000000010 /* CompatSystemSymbol.swift */"), 3)
        workflow = (ROOT / ".github/workflows/ios15-m0-baseline.yml").read_text()
        self.assertIn("scripts/test_ios15_sf_symbols.py", workflow)
        self.assertIn("bash scripts/check_ios15_symbols.sh", workflow)
        self.assertIn("tree-sitter-swift==0.7.3", workflow)

    def test_production_render_boundaries_have_no_bypass(self):
        rows = audit(ROOT, self.catalog)
        summary = [f"{row['path']}:{call['line']} {call['expression']}"
                   for row in rows for call in row["calls"]]
        self.assertEqual(summary, [], "unprotected symbol rendering: " + "; ".join(summary))


if __name__ == "__main__":
    unittest.main()
