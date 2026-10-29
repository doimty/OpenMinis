#!/usr/bin/env python3
"""Audit SF Symbol render boundaries; emit exact edits, never modify Swift.

Use tree-sitter==0.25.2 and tree-sitter-swift==0.7.3 (also used by the
compatibility planner). The catalog is offline test data, not an app asset.
"""
import argparse
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parent.parent
CATALOG = ROOT / "scripts/fixtures/sf-symbols-availability.json"
OWNER = "src/ios/Shared/CompatSystemSymbol.swift"
EXCLUDED = {"MinisTests", "MinisUITests", "AgentWidget", "FileProvider"}
RENDER_APIS = {"Image", "SwiftUI.Image", "UIImage", "Label", "SwiftUI.Label"}
UPSTREAM_SHA = "cb2e670a213ff42ae08528ee2c401bfb1d799675"


def version(value):
    return tuple(int(x) for x in value.split("."))


def make_catalog(source_dir):
    names = {}
    for path in sorted(source_dir.glob("SFSymbol+*.swift")):
        if "AllSymbols" in path.name:
            continue
        text = path.read_text()
        match = re.search(r"@available\(iOS ([0-9.]+),", text)
        if not match:
            continue
        introduced = match[1]
        for name in re.findall(r'rawValue: "([^"\n]+)"', text):
            if name not in names or version(introduced) < version(names[name]):
                names[name] = introduced
    if len(names) < 7000 or names.get("mic.and.signal.meter") != "16.0":
        raise ValueError("incomplete/unexpected catalog input")
    return {
        "source": "https://github.com/SFSafeSymbols/SFSafeSymbols",
        "source_commit": UPSTREAM_SHA,
        "license": "MIT; see sf-symbols-LICENSE.txt",
        "scope": "Generated name-introduction metadata; NOT an iOS runtime test",
        "introduced_ios": dict(sorted(names.items())),
    }


def safe_expression(node, catalog):
    if node.type == "line_string_literal":
        text = node.text.decode()
        name = text[1:-1]
        return name in catalog and version(catalog[name]) <= (15, 0)
    # A fixed toggle between two old symbols needs no adapter. Never mistake
    # its condition for the returned name, or assume a dynamic string is safe.
    if node.type == "ternary_expression":
        return all(safe_expression(child, catalog) for child in node.named_children[-2:])
    return node.type == "call_expression" and node.text.startswith(b"CompatSystemSymbol.name(")


def unsafe_calls(data, catalog, parser):
    pending = [parser.parse(data).root_node]
    found = []
    while pending:
        node = pending.pop()
        pending.extend(reversed(node.named_children))
        if node.type != "value_argument" or len(node.named_children) < 2:
            continue
        label, expression = node.named_children[0], node.named_children[-1]
        if label.type != "value_argument_label" or label.text not in {b"systemName", b"systemImage"}:
            continue
        call = node.parent.parent.parent
        if call.type != "call_expression":
            continue
        api = call.named_children[0].text.decode()
        if api not in RENDER_APIS or safe_expression(expression, catalog):
            continue
        found.append({"line": expression.start_point.row + 1, "api": api,
                      "expression": expression.text.decode(),
                      "start": expression.start_byte, "end": expression.end_byte})
    return sorted(found, key=lambda row: row["start"])


def exact_edits(data, calls):
    """Unique, non-overlapping original-text regions for the edit tool."""
    replacements = [(row["start"], row["end"],
                     b"CompatSystemSymbol.name(" + data[row["start"]:row["end"]] + b")")
                    for row in calls]
    regions = []
    for start, end, _ in replacements:
        low = data.rfind(b"\n", 0, start) + 1
        high = data.find(b"\n", end)
        high = len(data) if high < 0 else high + 1
        while data.count(data[low:high]) != 1:
            low = data.rfind(b"\n", 0, max(0, low - 1)) + 1
            next_line = data.find(b"\n", high)
            high = len(data) if next_line < 0 else next_line + 1
        if regions and low <= regions[-1][1]:
            regions[-1] = (min(low, regions[-1][0]), max(high, regions[-1][1]))
        else:
            regions.append((low, high))
    # Uniqueness expansion can reach a previous expanded block.
    merged = []
    for low, high in regions:
        while merged and low <= merged[-1][1]:
            a, b = merged.pop()
            low, high = min(low, a), max(high, b)
        merged.append((low, high))
    edits = []
    for low, high in merged:
        old = data[low:high]
        new = old
        for start, end, value in reversed(replacements):
            if low <= start and end <= high:
                new = new[:start-low] + value + new[end-low:]
        assert data.count(old) == 1
        edits.append({"oldText": old.decode(), "newText": new.decode()})
    return edits


def audit(root, catalog):
    from tree_sitter import Language, Parser
    import tree_sitter_swift
    parser = Parser(Language(tree_sitter_swift.language()))
    reports = []
    for path in sorted((root / "src/ios").rglob("*.swift")):
        relative = str(path.relative_to(root))
        if relative == OWNER or EXCLUDED.intersection(path.parts):
            continue
        data = path.read_bytes()
        calls = unsafe_calls(data, catalog, parser)
        if calls:
            reports.append({"path": relative, "calls": calls, "edits": exact_edits(data, calls)})
    return reports


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--catalog-source", type=Path)
    ap.add_argument("--catalog", type=Path, default=CATALOG)
    ap.add_argument("--root", type=Path, default=ROOT)
    ap.add_argument("--plan", type=Path)
    args = ap.parse_args()
    if args.catalog_source:
        data = make_catalog(args.catalog_source)
        args.catalog.parent.mkdir(parents=True, exist_ok=True)
        args.catalog.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
        print(f"Catalog: {len(data['introduced_ios'])} names at {UPSTREAM_SHA}")
        return 0
    catalog = json.loads(args.catalog.read_text())["introduced_ios"]
    reports = audit(args.root, catalog)
    if args.plan:
        args.plan.write_text(json.dumps(reports, ensure_ascii=False, indent=2) + "\n")
    count = sum(len(row["calls"]) for row in reports)
    for row in reports:
        for call in row["calls"]:
            print(f"{row['path']}:{call['line']}: {call['api']}: {call['expression']}")
    print(f"{'FAIL' if reports else 'PASS'}: {count} unprotected late/dynamic symbol render calls in {len(reports)} files")
    return int(bool(reports))


if __name__ == "__main__":
    sys.exit(main())
