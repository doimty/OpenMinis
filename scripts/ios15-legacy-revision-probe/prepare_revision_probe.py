#!/usr/bin/env python3
"""Generate baseline/C1/C3 legacy-observation probe sources without editing production."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

SOURCE_PATHS = (
    "src/ios/Shared/LegacyFlowLayout.swift",
    "src/ios/Shared/LegacyHostingContent.swift",
    "src/ios/Agent/MessageList/MessageListLayout.swift",
    "src/ios/Agent/MessageList/MessageListInfrastructure.swift",
)
SPLIT = "// MARK: - Cell State Bridge"
GATE = "if #available(iOS 16.0, *) {"
ROUTING = "if #available(iOS 16.0, *), !ProbeSettings.forceLegacy {"
GETTER_HEAD = "\n    // === BEGIN REVISION PROBE READONLY GETTERS ==="
GETTER_TAIL = "    // === END REVISION PROBE READONLY GETTERS ===\n"

CLEAR_OLD = """    func clearCachedHeight() {
        lastComputedHeight = nil
        lastComputedWidth = nil
        lastMeasureMediaTime = nil
        legacyMeasuredSize = nil
    }"""
CLEAR_C1 = """    func clearCachedHeight() {
        lastComputedHeight = nil
        lastComputedWidth = nil
        lastMeasureMediaTime = nil
        // [revision-probe C1] Preserve the same-owner async observation.
        // applyContentConfiguration still clears it for a real content swap.
        seededHeight = nil
        seededWidth = nil
    }"""

GETTER_BLOCK = """
    // === BEGIN REVISION PROBE READONLY GETTERS ===
    // Probe-only surface. Production paths never call this accessor.
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
    // === END REVISION PROBE READONLY GETTERS ===
"""

LEGACY_OLD_PREFERENCE = """private struct LegacyHostedSizeKey: PreferenceKey {
    static var defaultValue: CGSize = .zero
    static func reduce(value: inout CGSize, nextValue: () -> CGSize) { value = nextValue() }
}

private struct LegacyHostedRoot: View {
    let content: AnyView
    let onSizeChange: (CGSize) -> Void

    var body: some View {
        content
            .fixedSize(horizontal: false, vertical: true)
            // [T-ios15-legacyhost-measure] overlay, not background: a background
            // GeometryReader sits BEHIND the hosted content and on some legacy
            // layout passes reports the parent's proposal (the cell's current
            // estimate, e.g. 40pt) instead of the content's fixedSize ideal
            // height (e.g. 96pt). The callback then writes the estimate back
            // into the cell and the collection view never converges — the
            // composer probe's collection-initial/grow/shrink all stayed red
            // with the cell pinned at the 40pt estimate while the host view
            // rendered at 96pt. overlay is stacked ABOVE the already-laid-out
            // content and reports the real rendered size.
            .overlay(
                GeometryReader { proxy in
                    Color.clear.preference(key: LegacyHostedSizeKey.self, value: proxy.size)
                }
            )
            .onPreferenceChange(LegacyHostedSizeKey.self, perform: onSizeChange)
    }
}"""

LEGACY_C3_PREFERENCE = """private struct LegacyHostedSize: Equatable {
    let generation: UInt
    let size: CGSize
}

private struct LegacyHostedSizeKey: PreferenceKey {
    static var defaultValue = LegacyHostedSize(generation: 0, size: .zero)
    static func reduce(value: inout LegacyHostedSize, nextValue: () -> LegacyHostedSize) {
        value = nextValue()
    }
}

private struct LegacyHostedRoot: View {
    let content: AnyView
    let generation: UInt
    let onSizeChange: (LegacyHostedSize) -> Void

    var body: some View {
        content
            .fixedSize(horizontal: false, vertical: true)
            // [T-ios15-legacyhost-measure] overlay, not background: a background
            // GeometryReader sits BEHIND the hosted content and on some legacy
            // layout passes reports the parent's proposal (the cell's current
            // estimate, e.g. 40pt) instead of the content's fixedSize ideal
            // height (e.g. 96pt). The callback then writes the estimate back
            // into the cell and the collection view never converges — the
            // composer probe's collection-initial/grow/shrink all stayed red
            // with the cell pinned at the 40pt estimate while the host view
            // rendered at 96pt. overlay is stacked ABOVE the already-laid-out
            // content and reports the real rendered size.
            .overlay(
                GeometryReader { proxy in
                    Color.clear.preference(
                        key: LegacyHostedSizeKey.self,
                        value: LegacyHostedSize(generation: generation, size: proxy.size))
                }
            )
            .onPreferenceChange(LegacyHostedSizeKey.self, perform: onSizeChange)
    }
}"""

LEGACY_OLD_ROOT_ASSIGN = """        host.rootView = AnyView(LegacyHostedRoot(content: current.content) { [weak self] size in
            self?.contentSizeChanged(size)
        })"""
LEGACY_C3_ROOT_ASSIGN = """        let generation = configurationGeneration
        host.rootView = AnyView(LegacyHostedRoot(content: current.content, generation: generation) { [weak self] payload in
            self?.contentSizeChanged(payload)
        })"""

LEGACY_OLD_CALLBACK = """    func contentSizeChanged(_ size: CGSize) {
        guard size.width > 1, size.height.isFinite,
              abs(size.width - lastSize.width) > 0.5 || abs(size.height - lastSize.height) > 0.5 else { return }
        lastSize = size"""
LEGACY_C3_CALLBACK = """    private func contentSizeChanged(_ payload: LegacyHostedSize) {
        // Reject a delayed preference produced by a superseded root before it
        // can update lastSize or enter the current configuration's callback.
        guard payload.generation == configurationGeneration else { return }
        let size = payload.size
        guard size.width > 1, size.height.isFinite,
              abs(size.width - lastSize.width) > 0.5 || abs(size.height - lastSize.height) > 0.5 else { return }
        lastSize = size"""


def restore_legacy(text: str) -> str:
    for new, old in ((LEGACY_C3_PREFERENCE, LEGACY_OLD_PREFERENCE),
                     (LEGACY_C3_ROOT_ASSIGN, LEGACY_OLD_ROOT_ASSIGN),
                     (LEGACY_C3_CALLBACK, LEGACY_OLD_CALLBACK)):
        if text.count(new) != 1:
            raise ValueError("C3 legacy marker drifted")
        text = text.replace(new, old)
    return text


def restore_infrastructure(text: str) -> str:
    if text.count(ROUTING) != 1:
        raise ValueError("routing marker drifted")
    text = text.replace(ROUTING, GATE)
    if text.count(GETTER_HEAD) != 1 or text.count(GETTER_TAIL) != 1:
        raise ValueError("getter markers drifted")
    start = text.index(GETTER_HEAD)
    end = text.index(GETTER_TAIL) + len(GETTER_TAIL)
    return text[:start] + text[end:]


def make_infrastructure(source: str, variant: str) -> str:
    if source.count(SPLIT) != 1:
        raise ValueError("Cell State Bridge split marker drifted")
    infra = source.split(SPLIT, 1)[0]
    if infra.count(GATE) != 1 or infra.count(CLEAR_OLD) != 1:
        raise ValueError("infrastructure anchors drifted")
    infra = infra.replace(GATE, ROUTING)
    anchor = "    override func prepareForReuse() {"
    if infra.count(anchor) != 1:
        raise ValueError("prepareForReuse anchor drifted")
    infra = infra.replace(anchor, GETTER_BLOCK + anchor)
    if variant in ("c1", "c3"):
        infra = infra.replace(CLEAR_OLD, CLEAR_C1)
    if infra.count(CLEAR_C1 if variant in ("c1", "c3") else CLEAR_OLD) != 1:
        raise ValueError("clearCachedHeight generation failed")
    return infra


def make_legacy(source: str, variant: str) -> str:
    if variant != "c3":
        return source
    for old, new in ((LEGACY_OLD_PREFERENCE, LEGACY_C3_PREFERENCE),
                     (LEGACY_OLD_ROOT_ASSIGN, LEGACY_C3_ROOT_ASSIGN),
                     (LEGACY_OLD_CALLBACK, LEGACY_C3_CALLBACK)):
        if source.count(old) != 1:
            raise ValueError("LegacyHostingContent anchor drifted")
        source = source.replace(old, new)
    return source


def prepare(root: Path, out: Path, probe_dir: Path | None = None) -> dict:
    root, out = Path(root), Path(out)
    probe_dir = Path(probe_dir) if probe_dir else Path(__file__).resolve().parent
    out.mkdir(parents=True, exist_ok=True)
    source = {p: (root / p).read_text() for p in SOURCE_PATHS}
    variants = {}
    for variant in ("baseline", "c1", "c3"):
        infra = make_infrastructure(source[SOURCE_PATHS[3]], variant)
        legacy = make_legacy(source[SOURCE_PATHS[1]], variant)
        (out / f"ProductionInfrastructure-{variant}.swift").write_text(infra)
        (out / f"LegacyHostingContent-{variant}.swift").write_text(legacy)
        variants[variant] = {
            "infrastructure_sha256": hashlib.sha256(infra.encode()).hexdigest(),
            "legacy_hosting_sha256": hashlib.sha256(legacy.encode()).hexdigest(),
        }
    for p in (SOURCE_PATHS[0], SOURCE_PATHS[2]):
        (out / Path(p).name).write_text(source[p])
    probe_files = ("ProbeApp.swift", "run_revision_probe.sh", "prepare_revision_probe.py",
                   "test_revision_probe.py", "README.md")
    manifest = {
        "source_sha256": {p: hashlib.sha256((root / p).read_bytes()).hexdigest() for p in SOURCE_PATHS},
        "probe_sha256": {p: hashlib.sha256((probe_dir / p).read_bytes()).hexdigest()
                         for p in probe_files if (probe_dir / p).is_file()},
        "variants": variants,
        "generated_edits": {
            "all": ["force legacy routing in generated infrastructure", "add read-only cache/generation getter"],
            "c1": ["clear computed and seed state, preserve same-owner legacyMeasuredSize"],
            "c3": ["C1 infrastructure", "LegacyHostedSize Equatable payload carries configurationGeneration and proxy.size",
                   "current-generation guard before lastSize dedup and callback delivery"],
        },
        "legacy_payload_contract": {
            "measurement_source": "GeometryReader proxy.size, unchanged",
            "equatable_fields": ["generation", "size"],
            "old_generation_policy": "drop before lastSize mutation and callback",
            "same_size_new_generation_policy": "payload differs by generation and can rearm onPreferenceChange",
        },
        "limits": "Source-derived collection/legacy probe on pinned iOS26.2 with forced legacy routing; no production edit, no synchronous measurement, no UI acceptance.",
    }
    (out / "revision-extraction.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("output", type=Path)
    ap.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    args = ap.parse_args()
    print(json.dumps(prepare(args.root, args.output), indent=2))
    print("PASS: source-derived baseline/C1/C3 revision extraction")
