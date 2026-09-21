#!/usr/bin/env python3
"""Extract only production source needed by the diagnostic width probe.

This deliberately does not read or copy the private conversation export. The
Swift runner uses a neutral five-code-block structural fixture.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

CODE_START = "final class CodeBlockAttachment: NSTextAttachment {"
CODE_END = "\n// MARK: - Table Attachment"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def extract(root: Path, out: Path) -> dict:
    source_path = root / "src/ios/Views/Chat/SelectableMarkdownView.swift"
    host_path = root / "src/ios/Shared/LegacyHostingContent.swift"
    guard_h = root / "src/ios/Shared/NSTextContainerSetSizeGuard.h"
    guard_m = root / "src/ios/Shared/NSTextContainerSetSizeGuard.m"
    source = source_path.read_text()
    if source.count(CODE_START) != 1 or source.count(CODE_END) != 1:
        raise ValueError("CodeBlockAttachment extraction anchors drifted")
    start = source.index(CODE_START)
    end = source.index(CODE_END, start)
    attachment = source[start:end]
    if "attachmentBounds(for" not in attachment or "measureCodeHeight" not in attachment:
        raise ValueError("extracted attachment lost its measurement path")
    if "1521" in attachment or "1354" in attachment or "1865" in attachment or "1484" in attachment:
        raise ValueError("observed device heights must not enter the production-derived fixture")
    out.mkdir(parents=True, exist_ok=True)
    (out / "CodeBlockAttachment.swift").write_text("import UIKit\nimport ObjectiveC\n\n" + attachment + "\n")
    for path in (host_path, guard_h, guard_m):
        (out / path.name).write_bytes(path.read_bytes())
    commit = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
    manifest = {
        "kind": "source-derived-diagnostic-width-probe-inputs",
        "commit": commit,
        "source_sha256": {
            str(source_path.relative_to(root)): sha(source_path.read_bytes()),
            str(host_path.relative_to(root)): sha(host_path.read_bytes()),
            str(guard_h.relative_to(root)): sha(guard_h.read_bytes()),
            str(guard_m.relative_to(root)): sha(guard_m.read_bytes()),
        },
        "attachment_excerpt_sha256": sha(attachment.encode()),
        "attachment_source": "CodeBlockAttachment copied byte-for-byte from production source between stable MARK anchors",
        "fixture": "neutral five-code-block structural fixture; no original conversation text included",
        "limits": [
            "component/API-path probe, not full Markdown renderer or full App",
            "forced legacy hosting path on pinned iOS26.2 runtime, not iOS15 runtime",
            "native result cannot by itself prove the exact device row height producer",
        ],
    }
    (out / "inputs.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("root", type=Path)
    ap.add_argument("output", type=Path)
    args = ap.parse_args()
    print(json.dumps(extract(args.root, args.output), indent=2))
