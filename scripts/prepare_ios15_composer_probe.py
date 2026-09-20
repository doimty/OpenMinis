#!/usr/bin/env python3
"""Source-derived native fixtures. No production file is edited by this script."""
import argparse
import hashlib
import json
from pathlib import Path


def between(source, start, end):
    if source.count(start) != 1:
        raise ValueError(f"expected unique start: {start}")
    tail = source.split(start, 1)[1]
    if end not in tail:
        raise ValueError(f"missing end: {end}")
    return start + tail.split(end, 1)[0]


def prepare(root, out):
    out.mkdir(parents=True, exist_ok=True)
    source_paths = [
        "src/ios/Views/Chat/AIChatView.swift",
        "src/ios/Views/Chat/ChatInputBar.swift",
        "src/ios/Shared/LegacyFlowLayout.swift",
        "src/ios/Shared/LegacyHostingContent.swift",
        "src/ios/Agent/MessageList/MessageListInfrastructure.swift",
        "src/ios/Agent/MessageList/MessageListLayout.swift",
        "src/ios/Shared/CompatGeometry.swift",
    ]
    source = {p: (root / p).read_text() for p in source_paths}
    infra = source[source_paths[4]].split("// MARK: - Cell State Bridge", 1)[0]
    gate = "if #available(iOS 16.0, *) {"
    # Exactly one routing edit, in applyHostedContent. Sizing, cache and hit
    # testing implementations remain byte-for-byte from the production source.
    if infra.count(gate) != 1:
        raise ValueError("SelfSizingCell availability gate drifted")
    infra = infra.replace(gate, "if #available(iOS 16.0, *), !ProbeSettings.forceLegacy {")
    (out / "ProductionInfrastructure.swift").write_text(infra)
    grid = between(source[source_paths[1]], "struct InputAttachmentGridView: View {",
                   "// MARK: - Attachment Chip")
    if grid.count(gate) != 1:
        raise ValueError("InputAttachmentGridView availability gate drifted")
    grid = grid.replace(gate, "if #available(iOS 16.0, *), !ProbeSettings.forceLegacy {")
    # FlowLayout is needed to type-check the original native branch even when
    # the probe selects the legacy branch at runtime.
    chat = source[source_paths[1]]
    native_start = chat.index("@available(iOS 16.0, *)")
    native_end = chat.index("// MARK: - Input Attachment Grid")
    native = chat[native_start:native_end]
    (out / "ProductionGrid.swift").write_text("import SwiftUI\nimport UniformTypeIdentifiers\n" + native + grid)
    attachment = between(source[source_paths[0]],
                         "                // Attachment preview chips — 3 per row",
                         "                inputFieldOrWaveform")
    template = (root / "scripts/ios15-composer-probe/ComposerFixture.swift.in").read_text()
    if template.count("__PRODUCTION_ATTACHMENT_SECTION__") != 1:
        raise ValueError("composer fixture marker drifted")
    surface = between(source[source_paths[0]], "private struct ComposerSurface: ViewModifier {",
                      "// MARK: - Provider Import Prompt")
    surface = surface.replace("if #available(iOS 26.0, *) {",
                              "if #available(iOS 26.0, *), !ProbeSettings.forcePre26Surface {")
    (out / "ComposerFixture.swift").write_text(template.replace("__PRODUCTION_ATTACHMENT_SECTION__", attachment) + "\n" + surface)
    geometry = source[source_paths[6]]
    if geometry.count(gate) != 1:
        raise ValueError("CompatGeometry availability gate drifted")
    (out / "ProductionGeometry.swift").write_text(geometry.replace(gate,
        "if #available(iOS 16.0, *), !ProbeSettings.forceLegacyGeometry {"))
    hashes = {p: hashlib.sha256((root / p).read_bytes()).hexdigest() for p in source_paths}
    manifest = {
        "source_sha256": hashes,
        "generated_sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                              for p in out.glob("*.swift")},
        "routing_edits": ["force legacy in applyHostedContent", "force legacy in InputAttachmentGridView",
                          "select legacy/native CompatGeometry", "force pre26 ComposerSurface"],
        "substitutions": ["fixed 64pt attachment chip, no image/file I/O", "unrelated input field/bottom row",
                          "logger and unused Markdown/table types", "fixed-height SwiftUI collection row content"],
        "limits": "Production layout/cell paths on pinned iOS26.2, not iOS15 or full-app button acceptance.",
    }
    (out / "extraction.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    args = parser.parse_args()
    prepare(args.root, args.output)
    print("PASS: source-derived composer/cell fixture extraction")
