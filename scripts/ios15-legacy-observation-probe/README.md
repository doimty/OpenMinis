# Collection/legacy cache-observation probe (source-derived, read-only)

Diagnostic probe for the OpenMinis iOS 15 legacy hosting height path. It compiles
REAL production sources — `MessageListInfrastructure` (everything before
`// MARK: - Cell State Bridge`), `MessageListLayout`, `LegacyHostingContent`,
`LegacyFlowLayout` — together with a small probe app, and runs two variants on
the pinned iOS 26.2 simulator with the legacy branch forced through a single
generated routing edit. No production file is edited; no synchronous measurement
is added; composer/attachments are removed.

This package was prepared under
`reports/openminis-ios15/capture-974a879d/observation-probe/` and reviewed into
`scripts/ios15-legacy-observation-probe/`. The parent review fixed a nested
snapshot getter compile issue, corrected hostFrame to measure the actual hosted
UI view in collection coordinates (not the outer LegacyHostingContentView), and
hardened nonce/case/row/frame/control/hypothesis validation. No src file changed.

## Files

- `prepare_observation_probe.py` — extracts production sources, writes
  `ProductionInfrastructure-baseline.swift` (production + forced-legacy routing
  + probe read-only cache getters) and `ProductionInfrastructure-candidate.swift`
  (baseline + `clearCachedHeight` that clears computed/seed but preserves the
  same-owner `legacyMeasuredSize`), plus byte copies of the three companion
  sources and an `extraction.json` manifest (source/generated/probe hashes,
  declared edit list). Restoring the routing edit and stripping the getter block
  yields byte-identical production source.
- `ProbeApp.swift` — native driver: real `MessageListViewController`,
  diffable data source, real legacy hosting cells with a fixed-height row model.
  Only unused logger/markdown/table types are stubbed.
- `run_observation_probe.sh` — macOS runner. Pins Xcode 26.2 / build 17C52 /
  iOS 26.2 SDK + runtime, requires a NEW/EMPTY output dir, compiles and launches
  both variants with fresh UUID nonces, copies only the nonce-scoped report from
  the app container, validates structure, and writes a side-by-side summary.
  It never treats `simctl` exit code as the verdict.
- `test_observation_probe.py` — extraction/validator contracts (runnable on
  Linux): restore-equality to production bytes, candidate diff limited to the
  declared `clearCachedHeight` edit, getters present in both variants, no
  `systemLayoutSizeFitting`/`setCachedHeight`/`preferredLayoutAttributesFitting`
  calls in the driver, `seedMeasuredHeight` used exactly once, report validator
  (nonce/variant/runtime/controls/cases/snapshot fields).
- `README.md` — this file.

## Hypotheses under test (recorded, not pre-judged)

- **C1 starvation**: generic `clearCachedHeight` also nils `legacyMeasuredSize`
  (the only asynchronous size observation) while `LegacyHostingContent` dedups
  equal sizes, so an unchanged-content invalidation can leave the row at the
  layout estimate (cell 32 / host 79.333 shape seen in capture 974a879d).
- **C2 stale seed**: `seededHeight` survives `clearCachedHeight` and the seed
  short-circuit runs before the legacy branch.
- **C3 first-event rejection**: `window != nil` / generation guards can drop the
  first observation.
- **C4 same-size reconfigure**: re-applying identical content may or may not
  re-deliver the observation edge.

## Cases

1. `initial` — real first layout; both rows must settle at the real height
   (control).
2. `clear-same-content` — clear + `invalidateHeight` + `invalidateLayout` with
   unchanged content; row 0 observed for starvation vs recovery.
3. `recovery-grow` — real height change via fresh `applyHostedContent`; both
   rows must recover (control).
4. `same-size-reconfigure` — identical height re-applied; require both matching
   frames AND a rearmed legacy observation. A still-correct cached frame alone
   is not proof that the observation edge was delivered.
5. `seed-recovery-control` — change to120pt and require fresh legacy observations
   before testing the seed, so same-size-reconfigure loss cannot contaminate C2.
6. `seed-invalidation` — seed176 then generic clear; observe whether it survives
   (baseline) or returns120 (candidate). Both rows remain in the viewport. The
   original999pt seed virtualized row0 away and invalidated the two-row fixture.

## Verdict semantics

- **INVALID** (never bug-red): compile failure, launch failure/timeout, missing
  report, wrong nonce/runtime, or a lost control case (`initial`,
  `recovery-grow`, `seed-recovery-control` not green in either variant).
- Hypothesis cases are recorded per variant with `hypothesis_match`; the final
  `summary.json` compares baseline vs candidate side by side. Root-cause
  judgment is left to the primary reviewer.

## Run (macOS, pinned toolchain)

```bash
bash scripts/ios15-legacy-observation-probe/run_observation_probe.sh \
  "$GITHUB_WORKSPACE" "$RUNNER_TEMP/observation-probe"
```

Requires: Xcode 26.2 (build 17C52), iOS 26.2 simulator SDK and runtime, macOS 26
runner image (mirrors the existing `ios15-composer-layout-probe` workflow
pattern). Suggested workflow stanza mirrors that workflow's pins and artifact
upload of the whole output directory (`if-no-files-found: warn`,
`retention-days: 14`).

## Limits

Forced-legacy production source on an iOS 26.2 runtime — this exercises the iOS
15 code branch without pretending to be an iOS 15 device. It observes cache and
frame state only; it is not a UI layout verdict and does not claim the footer
occlusion is fixed.