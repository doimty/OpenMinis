#!/usr/bin/env python3
"""Run with the planner's existing tree-sitter environment; not a Swift compiler."""
import unittest
from tree_sitter import Language, Parser
import tree_sitter_swift
from plan_ios15_adapters import toolbar_conditionals


class ToolbarAuditTests(unittest.TestCase):
    def matches(self, source):
        parser = Parser(Language(tree_sitter_swift.language()))
        return toolbar_conditionals(source.encode(), parser)

    def test_optional_item_is_reported(self):
        self.assertEqual(len(self.matches('''
            struct Example: View { var body: some View {
                Text("Editor").toolbar {
                    if dirty { ToolbarItem { Button("Save") {} } }
                }
            }}''')), 1)

    def test_conditional_view_inside_item_is_supported(self):
        self.assertEqual(self.matches('''
            struct Example: View { var body: some View {
                Text("Editor").toolbar {
                    ToolbarItem { if dirty { Button("Save") {} } }
                }
            }}'''), [])

    def test_named_toolbar_builder_is_reported(self):
        self.assertEqual(len(self.matches('''
            struct Example: View {
                @ToolbarContentBuilder var items: some ToolbarContent {
                    if multi { ToolbarItem { Text("Add") } }
                    else { ToolbarItem { Text("Done") } }
                }
            }''')), 1)

    def test_menu_conditions_are_not_toolbar_conditions(self):
        self.assertEqual(self.matches('''
            struct Example: View { var body: some View {
                Text("Editor").toolbar {
                    ToolbarItem { Menu("Actions") { if dirty { Button("Save") {} } } }
                }
            }}'''), [])

    def test_new_system_declaration_is_not_a_legacy_blocker(self):
        self.assertEqual(self.matches('''
            @available(iOS 16.0, *)
            struct Example: View { var body: some View {
                Text("Editor").toolbar { if dirty { ToolbarItem { Text("Save") } } }
            }}'''), [])

    def test_available_parent_view_branch_is_not_a_legacy_blocker(self):
        self.assertEqual(self.matches('''
            struct Example: View { var body: some View {
                if #available(iOS 16.0, *) {
                    Text("Editor").toolbar { if dirty { ToolbarItem { Text("Save") } } }
                } else { Text("Editor") }
            }}'''), [])

    def test_comments_and_strings_are_not_executable_builders(self):
        self.assertEqual(self.matches('''
            // .toolbar { if dirty { ToolbarItem { Text("Save") } } }
            let description = ".toolbar { if dirty { item } }"
            '''), [])


if __name__ == "__main__":
    unittest.main()
