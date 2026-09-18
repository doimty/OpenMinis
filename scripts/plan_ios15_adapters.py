#!/usr/bin/env python3
"""Emit exact, reviewable edits for already-defined iOS 15 UI adapters.

This planner NEVER modifies source. Run with the installed Swift tree-sitter
Python environment; inspect/apply its exact replacements with the editing tool.
It skips native availability branches and declarations, comments, and strings.
"""

import argparse
import json
from pathlib import Path
import re

from tree_sitter import Language, Parser
import tree_sitter_swift

RENAMES = {
    'NavigationStack': 'CompatNavigationStack',
    'LabeledContent': 'CompatLabeledContent',
    'presentationDetents': 'compatPresentationDetents',
    'presentationDragIndicator': 'compatPresentationDragIndicator',
    'onGeometryChange': 'compatOnGeometryChange',
}
OWNERS = {
    'SwiftUICompatibility.swift', 'CompatNavigationPath.swift', 'CompatPhotoPicker.swift',
    'CompatGeometry.swift', 'LegacyHostingContent.swift', 'LegacyFlowLayout.swift',
}
AVAILABLE = re.compile(rb'\biOS\s+(\d+)(?:\.\d+)?')


def introduced_after_15(text):
    match = AVAILABLE.search(text)
    return match is not None and int(match.group(1)) >= 16


def guarded(node, data):
    parent = node.parent
    while parent is not None:
        if parent.type in {'class_declaration', 'function_declaration', 'property_declaration', 'extension_declaration'}:
            for child in parent.named_children:
                if child.type == 'modifiers':
                    text = data[child.start_byte:child.end_byte]
                    if b'@available' in text and introduced_after_15(text):
                        return True
        if parent.type == 'if_statement':
            conditions = [c for c in parent.named_children if c.type == 'availability_condition']
            bodies = [c for c in parent.named_children if c.type == 'statements']
            if conditions and bodies and bodies[0].start_byte <= node.start_byte < bodies[0].end_byte:
                if any(introduced_after_15(data[c.start_byte:c.end_byte]) for c in conditions):
                    return True
        parent = parent.parent
    return False


def toolbar_conditionals(data, parser):
    """Find conditionals owned by ToolbarContentBuilder, not an item's ViewBuilder.

    Availability scanning is an aid only; the Apple compiler remains the gate.
    A nested Button/Menu/ToolbarItem closure owns its own ViewBuilder, so stop
    at the nearest closure instead of flagging every `if` beneath a toolbar.
    """
    stack = [parser.parse(data).root_node]
    found = []
    while stack:
        node = stack.pop()
        stack.extend(reversed(node.children))
        if node.type not in {'if_statement', 'switch_statement'} or guarded(node, data):
            continue
        parent = node.parent
        owner = None
        while parent is not None:
            if parent.type == 'lambda_literal':
                suffix = parent.parent
                call = suffix.parent if suffix else None
                if call and call.type == 'call_expression':
                    nav = next((c for c in call.named_children if c.type == 'navigation_expression'), None)
                    if nav:
                        tail = next((c for c in reversed(nav.named_children) if c.type == 'navigation_suffix'), None)
                        if tail and tail.text.strip() == b'.toolbar':
                            owner = 'toolbar closure'
                break
            if parent.type in {'property_declaration', 'function_declaration'}:
                if any(c.type == 'modifiers' and b'@ToolbarContentBuilder' in c.text for c in parent.named_children):
                    owner = 'ToolbarContentBuilder declaration'
                break
            parent = parent.parent
        if owner:
            found.append({'line': node.start_point.row + 1, 'owner': owner,
                          'condition': node.text.splitlines()[0].decode('utf-8')})
    return found


def replacements(data, parser):
    root = parser.parse(data).root_node
    stack = [root]
    found = []
    while stack:
        node = stack.pop()
        stack.extend(reversed(node.children))
        if node.type != 'simple_identifier':
            continue
        name = data[node.start_byte:node.end_byte].decode('utf-8')
        if name not in RENAMES and name not in {'scrollContentBackground', 'scrollDismissesKeyboard'}:
            continue
        if guarded(node, data):
            continue
        if name in {'NavigationStack', 'LabeledContent'}:
            if node.parent.type != 'call_expression':
                continue
            if name == 'NavigationStack' and not data[node.end_byte:].lstrip().startswith(b'{'):
                continue  # Typed-path stacks require a separate, reviewed migration.
        elif node.parent.type != 'navigation_suffix':
            continue
        if name == 'scrollContentBackground':
            match = re.match(rb'\(\s*\.hidden\s*\)', data[node.end_byte:])
            if match:
                found.append((node.start_byte, node.end_byte + match.end(), b'compatHiddenScrollBackground()', name))
        elif name == 'scrollDismissesKeyboard':
            match = re.match(rb'\(\s*\.interactively\s*\)', data[node.end_byte:])
            if match:
                found.append((node.start_byte, node.end_byte + match.end(), b'compatScrollDismissesKeyboardInteractively()', name))
        else:
            found.append((node.start_byte, node.end_byte, RENAMES[name].encode(), name))
    return sorted(found)


def exact_edits(data, changes):
    # Begin with the complete affected line, expanding only to disambiguate.
    regions = []
    for start, end, _, _ in changes:
        lo = data.rfind(b'\n', 0, start) + 1
        hi = data.find(b'\n', end)
        hi = len(data) if hi < 0 else hi + 1
        while data.count(data[lo:hi]) > 1:
            lo = data.rfind(b'\n', 0, max(0, lo - 1)) + 1
            next_end = data.find(b'\n', hi)
            hi = len(data) if next_end < 0 else next_end + 1
        regions.append([lo, hi])
    merged = []
    for lo, hi in sorted(regions):
        if merged and data[merged[-1][1]:lo].count(b'\n') <= 2:
            merged[-1][1] = max(merged[-1][1], hi)
        else:
            merged.append([lo, hi])
    result = []
    for lo, hi in merged:
        old = data[lo:hi]
        new = old
        for start, end, replacement, _ in reversed(changes):
            if lo <= start and end <= hi:
                new = new[:start-lo] + replacement + new[end-lo:]
        assert data.count(old) == 1, 'ambiguous edit'
        result.append({'oldText': old.decode('utf-8'), 'newText': new.decode('utf-8')})
    return result


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument('--output-dir', type=Path)
    mode.add_argument('--check-toolbars', action='store_true',
                      help='Report legacy-visible ToolbarContentBuilder conditionals and fail if any remain')
    args = ap.parse_args()
    repo = Path(__file__).resolve().parents[1]
    parser = Parser(Language(tree_sitter_swift.language()))
    if args.check_toolbars:
        findings = {}
        for path in sorted((repo / 'src/ios').rglob('*.swift')):
            if any(part in {'MinisTests', 'MinisUITests', 'AgentWidget', 'FileProvider'} for part in path.parts):
                continue
            matches = toolbar_conditionals(path.read_bytes(), parser)
            if matches:
                findings[str(path.relative_to(repo))] = matches
        print(json.dumps({'files': len(findings), 'findings': findings}, indent=2))
        raise SystemExit(bool(findings))
    plans = []
    counts = {}
    for path in sorted((repo / 'src/ios').rglob('*.swift')):
        if path.name in OWNERS or any(part in {'MinisTests', 'MinisUITests', 'AgentWidget', 'FileProvider'} for part in path.parts):
            continue
        data = path.read_bytes()
        changes = replacements(data, parser)
        if not changes:
            continue
        for _, _, _, name in changes:
            counts[name] = counts.get(name, 0) + 1
        plans.append({'path': str(path), 'edits': exact_edits(data, changes)})
    args.output_dir.mkdir(parents=True, exist_ok=True)
    batch, size, index = [], 0, 1
    for plan in plans:
        count = len(json.dumps(plan, ensure_ascii=False))
        if batch and size + count > 14000:
            (args.output_dir / f'batch-{index:02}.json').write_text(json.dumps(batch, ensure_ascii=False, indent=2) + '\n')
            index += 1
            batch, size = [], 0
        batch.append(plan)
        size += count
    if batch:
        (args.output_dir / f'batch-{index:02}.json').write_text(json.dumps(batch, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'files': len(plans), 'changes': counts, 'batches': index if plans else 0}, indent=2))


if __name__ == '__main__':
    main()
