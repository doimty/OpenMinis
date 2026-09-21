#!/usr/bin/env python3
"""Source-derived collection/legacy cache-observation probe. No production file is edited."""
import argparse
import hashlib
import json
from pathlib import Path

SOURCE_PATHS = [
    "src/ios/Shared/LegacyFlowLayout.swift",
    "src/ios/Shared/LegacyHostingContent.swift",
    "src/ios/Agent/MessageList/MessageListLayout.swift",
    "src/ios/Agent/MessageList/MessageListInfrastructure.swift",
]
SPLIT = "// MARK: - Cell State Bridge"
GATE = "if #available(iOS 16.0, *) {"
ROUTING_EDIT = "if #available(iOS 16.0, *), !ProbeSettings.forceLegacy {"

GETTER_HEAD = "\n    // === BEGIN PROBE READONLY GETTERS ==="
GETTER_TAIL = "    // === END PROBE READONLY GETTERS ===\n"

CLEAR_OLD = """    func clearCachedHeight() {
        lastComputedHeight = nil
        lastComputedWidth = nil
        lastMeasureMediaTime = nil
        legacyMeasuredSize = nil
    }"""
CLEAR_CANDIDATE = """    func clearCachedHeight() {
        lastComputedHeight = nil
        lastComputedWidth = nil
        lastMeasureMediaTime = nil
        // [observation-probe candidate] Same-owner generic invalidation keeps the
        // only asynchronous size observation (legacyMeasuredSize); the seed is
        // cleared so a stale memo cannot win. applyContentConfiguration still
        // clears legacyMeasuredSize when the content itself changes.
        seededHeight = nil
        seededWidth = nil
    }"""

GETTER_BLOCK = """
    // === BEGIN PROBE READONLY GETTERS ===
    // Probe-only observation surface. Never called by production paths; the
    // generated fixture's driver reads it to report cache/legacy/seed state.
    struct ProbeCacheSnapshot {
        let lastComputedHeight: CGFloat?
        let lastComputedWidth: CGFloat?
        let legacyMeasuredSize: CGSize?
        let seededHeight: CGFloat?
        let seededWidth: CGFloat?
        let configGeneration: UInt
        let hasWindow: Bool
        let inCollection: Bool
        var json: [String: Any] {
            [
                "lastComputedHeight": lastComputedHeight.map { Double($0) } as Any? ?? NSNull(),
                "lastComputedWidth": lastComputedWidth.map { Double($0) } as Any? ?? NSNull(),
                "legacyMeasuredHeight": legacyMeasuredSize.map { Double(ceil($0.height)) } as Any? ?? NSNull(),
                "seededHeight": seededHeight.map { Double($0) } as Any? ?? NSNull(),
                "seededWidth": seededWidth.map { Double($0) } as Any? ?? NSNull(),
                "configGeneration": Int(configGeneration),
                "hasWindow": hasWindow,
                "inCollection": inCollection,
            ]
        }
    }
    var probeCacheSnapshot: ProbeCacheSnapshot {
        ProbeCacheSnapshot(lastComputedHeight: lastComputedHeight,
                           lastComputedWidth: lastComputedWidth,
                           legacyMeasuredSize: legacyMeasuredSize,
                           seededHeight: seededHeight,
                           seededWidth: seededWidth,
                           configGeneration: configGeneration,
                           hasWindow: window != nil,
                           inCollection: superview is UICollectionView)
    }
    // === END PROBE READONLY GETTERS ===
"""


def restore_production(text):
    """Remove routing edit and probe getter block; returns production bytes."""
    if text.count(ROUTING_EDIT) != 1:
        raise ValueError("routing edit marker drifted")
    text = text.replace(ROUTING_EDIT, GATE)
    if text.count(GETTER_HEAD) != 1 or text.count(GETTER_TAIL) != 1:
        raise ValueError("getter block markers drifted")
    start = text.index(GETTER_HEAD)
    end = text.index(GETTER_TAIL) + len(GETTER_TAIL)
    return text[:start] + text[end:]


def prepare(root, out, probe_dir=None):
    root, out, probe_dir = Path(root), Path(out), Path(probe_dir) if probe_dir else Path(__file__).resolve().parent
    out.mkdir(parents=True, exist_ok=True)
    source = {p: (root / p).read_text() for p in SOURCE_PATHS}
    infra_full = source[SOURCE_PATHS[3]]
    if infra_full.count(SPLIT) != 1:
        raise ValueError("Cell State Bridge split marker drifted")
    infra = infra_full.split(SPLIT, 1)[0]
    if infra.count(GATE) != 1:
        raise ValueError("SelfSizingCell availability gate drifted")
    infra = infra.replace(GATE, ROUTING_EDIT)
    if infra.count(CLEAR_OLD) != 1:
        raise ValueError("clearCachedHeight body drifted")
    insert_before = "    override func prepareForReuse() {"
    if infra.count(insert_before) != 1:
        raise ValueError("prepareForReuse anchor drifted")
    baseline = infra.replace(insert_before, GETTER_BLOCK + insert_before)
    if baseline.count(CLEAR_OLD) != 1:
        raise ValueError("baseline clearCachedHeight lost")
    candidate = baseline.replace(CLEAR_OLD, CLEAR_CANDIDATE)
    if candidate.count(CLEAR_OLD) != 0 or candidate.count(CLEAR_CANDIDATE) != 1:
        raise ValueError("candidate clearCachedHeight replacement failed")

    for name, text in [("ProductionInfrastructure-baseline.swift", baseline),
                       ("ProductionInfrastructure-candidate.swift", candidate)]:
        (out / name).write_text(text)
    for p in SOURCE_PATHS[:3]:
        (out / Path(p).name).write_text(source[p])

    probe_files = ["ProbeApp.swift", "run_observation_probe.sh",
                   "prepare_observation_probe.py", "test_observation_probe.py", "README.md"]
    probe_sha = {name: hashlib.sha256((probe_dir / name).read_bytes()).hexdigest()
                 for name in probe_files if (probe_dir / name).is_file()}
    generated = {p.name: hashlib.sha256((out / p.name).read_bytes()).hexdigest()
                 for p in out.glob("*.swift")}
    manifest = {
        "source_sha256": {p: hashlib.sha256((root / p).read_bytes()).hexdigest() for p in SOURCE_PATHS},
        "generated_sha256": generated,
        "probe_sha256": probe_sha,
        "routing_edits": ["applyHostedContent: force legacy via ProbeSettings.forceLegacy (both variants)"],
        "observation_edits": ["SelfSizingCell probe read-only cache getters (both variants)"],
        "candidate_edits": ["clearCachedHeight: clear computed+seed only, preserve same-owner legacyMeasuredSize (candidate only)"],
        "substitutions": ["fixed-height SwiftUI row content", "unused logger/markdown/table stubs",
                          "composer/attachments removed"],
        "limits": "Production collection/layout/legacy-hosting paths on pinned iOS26.2 runtime with forced legacy branch; not iOS15 runtime, full app, or a UI layout verdict.",
    }
    (out / "extraction.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    args = parser.parse_args()
    manifest = prepare(args.root, args.output)
    print(json.dumps(manifest, indent=2))
    print("PASS: source-derived collection/legacy observation fixture extraction")