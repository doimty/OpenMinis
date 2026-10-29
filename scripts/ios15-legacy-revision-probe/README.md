# Legacy observation revision probe (baseline / C1 / C3)

Review-only generator improvement under
`reports/openminis-ios15/capture-c595b222/revision-probe/`. It extends the
primary reviewer's harness (`scripts/ios15-legacy-observation-probe/`, 21 local
tests, HEAD `bbca5a764c77f7840229a370fe55306c1dc12c64`) with a THIRD generated
variant that is not yet in the repo. The primary reviewer reviews, merges into
the diagnostic branch, and runs it in the cloud; nothing here edits `src`,
checks out a branch, commits, pushes, or starts CI.

## Why a third variant

Native run `35587424276` showed:

- `clear-same-content`: baseline cell40/host96/legacyNil vs candidate
  96/96/legacy96 → C1 (preserve the same-owner async observation in
  `clearCachedHeight`) is effective.
- `seed-invalidation`: baseline 176 (stale seed wins) vs candidate 120 → C2 is
  effective.
- `same-size-reconfigure`: BOTH variants end with `legacyNil/computedNil`,
  height 160 only because the layout cache still holds it →
  `hypothesis=false`. C1 alone is not a complete repair.

The remaining gap is the delivery edge: after a same-size configuration swap,
`applyContentConfiguration` clears `legacyMeasuredSize`, and the SwiftUI
`PreferenceKey` (`LegacyHostedSizeKey`) stores only `CGSize`, so an identical
size collapses the `onPreferenceChange` edge — the new generation never
re-delivers the observation.

## C3 generated-copy change (minimal)

Only the GENERATED `LegacyHostingContent-c3.swift` differs from production:

1. `LegacyHostedSize: Equatable { generation: UInt, size: CGSize }` — a
   self-contained measured payload. `size` is always the real
   `GeometryReader.proxy.size`; no fabricated or re-measured value.
2. `LegacyHostedSizeKey` preference now carries `LegacyHostedSize` instead of
   `CGSize`, so a same-size measurement from a NEW configuration carries a new
   generation and produces a fresh `onPreferenceChange` edge (Equatable
   compares generation+size).
3. `LegacyHostedRoot` receives the `configurationGeneration` captured in
   `updateRoot` and stamps every preference with it.
4. `contentSizeChanged(_ payload:)` rejects `payload.generation !=
   configurationGeneration` BEFORE mutating `lastSize` or entering the
   dedup/async delivery path — a delayed preference from a superseded root can
   no longer pollute the current configuration.

Explicitly NOT used: re-synchronous measurement, timer-based refresh, full tree
rebuild, or letting an old size masquerade as a new-generation measurement.

## Variants

- `baseline` — production `LegacyHostingContent.swift` byte-for-byte; generated
  infrastructure only adds the forced-legacy routing edit and the read-only
  cache getter. Restoring those yields byte-identical production source.
- `c1` — infrastructure with the C1 `clearCachedHeight` (clears computed+seed,
  keeps same-owner `legacyMeasuredSize`); legacy hosting is the production
  byte copy.
- `c3` — C1 infrastructure PLUS the generation-aware legacy hosting payload
  above.

All three compile the same `ProbeApp.swift` driver and the same six cases
(`initial`, `clear-same-content`, `recovery-grow`, `same-size-reconfigure`,
`seed-recovery-control`, `seed-invalidation`). `same-size-reconfigure`
requires BOTH the 160pt frames AND a re-delivered `legacyMeasuredHeight == 160`
(cache-only 160 is `hypothesis=false`).

## Files

- `prepare_revision_probe.py` — extraction/generation with source hashes,
  per-variant hashes, and a declared edit list (`generated_edits` /
  `legacy_payload_contract`). `restore_infrastructure` / `restore_legacy`
  strip the generated changes back to production bytes for the tests.
- `ProbeApp.swift` — three-variant native driver (unchanged downstream fixture
  patterns from the reviewed harness).
- `run_revision_probe.sh` — macOS runner. Pins Xcode 26.2/build 17C52 and iOS
  26.2 SDK+runtime; requires a NEW/EMPTY output dir; compiles and launches all
  three variants with fresh UUID nonces; copies only the nonce-scoped report
  from `Documents/legacy-revision-probe/<nonce>/`; validates structure;
  writes a side-by-side `summary.json`. Never treats `simctl` exit code as the
  verdict.
- `test_revision_probe.py` — extraction/validator contracts (`python3 -m
  unittest -v test_revision_probe.py`, runnable on Linux).

## Run

```bash
bash reports/openminis-ios15/capture-c595b222/revision-probe/run_revision_probe.sh \
  /root/.openclaw/workspace/repos/OpenMinis \
  "$RUNNER_TEMP/revision-probe"
```

## Verdict semantics

- **INVALID** (never bug-red): failed extraction/compile, launch timeout,
  missing report, wrong nonce/runtime, or any lost control (`initial`,
  `recovery-grow`, `seed-recovery-control` not green in any variant).
- Hypothesis cases are recorded per variant; root-cause judgment stays with the
  primary reviewer.

## Limits

Forced-legacy production-derived source on an iOS 26.2 runtime — it exercises
the iOS 15 code branch without pretending to be an iOS 15 device. It observes
cache and frame state only. The user-side V3 capture `c595b222` (8 samples,
7 target samples across 20.76s all showing 32/79.33, 419/561.33, footer
4/54.33, inspections stationary 0.0238pt from the true bottom) is accepted as
established evidence; no further user capture is requested. This probe does not
claim the UI layout is fixed.