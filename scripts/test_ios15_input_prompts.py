#!/usr/bin/env python3
"""Source gate for text controls silently omitted from iOS 15 alerts.

This is not a UI-rendering test. Native component and lifecycle tests are
separate, and the original failing runtime evidence is the device screenshot.
"""
from pathlib import Path
import sys
import unittest

from tree_sitter import Language, Parser
import tree_sitter_swift

ROOT = Path(__file__).resolve().parent.parent
PARSER = Parser(Language(tree_sitter_swift.language()))


def input_alert_sites(data):
    pending = [PARSER.parse(data).root_node]
    sites = []
    while pending:
        node = pending.pop()
        pending.extend(reversed(node.named_children))
        if node.type != "call_expression" or not node.named_children:
            continue
        called = node.named_children[0]
        if called.type != "navigation_expression" or not called.text.endswith(b".alert"):
            continue
        suffix = [child for child in node.named_children if child.type == "call_suffix"]
        if not suffix:
            continue
        children = [suffix[-1]]
        has_input = False
        while children:
            child = children.pop()
            children.extend(child.named_children)
            if child.type == "call_expression" and child.named_children:
                has_input |= child.named_children[0].text in {b"TextField", b"SecureField"}
        if has_input:
            sites.append(called.end_point.row + 1)
    return sorted(sites)


def production_violations():
    found = []
    for path in sorted((ROOT / "src/ios").rglob("*.swift")):
        if {"MinisTests", "MinisUITests"}.intersection(path.parts):
            continue
        for line in input_alert_sites(path.read_bytes()):
            found.append(f"{path.relative_to(ROOT)}:{line}")
    return found


class InputPromptContractTests(unittest.TestCase):
    def test_reported_new_group_pattern_is_red(self):
        data = b'view.alert("New Group", isPresented: $show) { TextField("Group name", text: $name); Button("Create") { save() } }'
        self.assertEqual(len(input_alert_sites(data)), 1)

    def test_multiline_and_presenting_inputs_are_detected(self):
        data = b'''view.alert(
  "Rename", isPresented: $show, presenting: item
) { target in
 TextField("Name", text: $name)
 TextField("Description", text: $description)
} message: { _ in Text("Help") }'''
        self.assertEqual(len(input_alert_sites(data)), 1)
        self.assertEqual(len(input_alert_sites(b'view.alert("Secret", isPresented: $show) { SecureField("Key", text: $key) }')), 1)

    def test_comments_sample_strings_and_button_only_alerts_are_not_inputs(self):
        data = b'''// view.alert("Fake") { TextField("Name", text: $name) }
let sample = """
view.alert("Fake") { TextField("Name", text: $name) }
"""
view.alert("Delete", isPresented: $show, presenting: item) { _ in Button("Delete") { remove() } }
view.alert(item: $item) { _ in Alert(title: Text("Info")) }
'''
        self.assertEqual(input_alert_sites(data), [])

    def test_compatibility_call_does_not_embed_input_in_raw_alert(self):
        data = b'view.compatTextInputAlert(Text("Name"), isPresented: $show, confirmLabel: Text("Save"), onConfirm: {}) { TextField("Name", text: $name) } message: { Text("Help") }'
        self.assertEqual(input_alert_sites(data), [])

    def test_all_original_callers_use_the_adapter(self):
        self.assertEqual(production_violations(), [])

    def test_legacy_does_not_mirror_owner_through_an_observer(self):
        source = (ROOT / "src/ios/Shared/CompatTextInputAlert.swift").read_text()
        self.assertIn("presentationRequested(byOwner: isPresented)", source)
        self.assertIn("didDismiss(ownerRequested: isPresented)", source)
        self.assertNotIn(".onChange", source, "a coalesced close/reopen must not lose presentation")
        self.assertNotIn(".synchronize", source)

    def test_native_alert_branch_is_gated_and_legacy_has_no_parent_dismiss(self):
        source = (ROOT / "src/ios/Shared/CompatTextInputAlert.swift").read_text()
        self.assertIn("if #available(iOS 16.0, *)", source)
        self.assertLess(source.index("if #available(iOS 16.0, *)"), source.index(".alert(title"))
        self.assertIn(".sheet(isPresented: sheetBinding", source)
        self.assertNotIn("@Environment(\\.dismiss)", source)
        self.assertNotIn("AppLocalized", source, "keep original SwiftUI localized/interpolated builders")
        self.assertNotIn("UIAlertController", source, "the fallback deliberately uses a normal Form")


if __name__ == "__main__":
    if "--audit" in sys.argv:
        violations = production_violations()
        print("\n".join(violations))
        print(f"{'FAIL' if violations else 'PASS'}: {len(violations)} raw text-input alerts")
        raise SystemExit(int(bool(violations)))
    unittest.main()
