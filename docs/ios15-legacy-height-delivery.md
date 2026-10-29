# iOS 15 legacy height delivery repair

## Locked baseline and scope

Pre-fix source: `8b601279c962f9b37e1e1b1f32a857cef98fafb1`.
Branch: `fix/ios15-legacy-height-delivery`.
Production scope: `LegacyHostingContent.swift` and `MessageListInfrastructure.swift` only. Keep the iOS 16 hosting path, row spacing, clipping, composer, CLI and unrelated project state unchanged.

## Hypothesis and acceptance signals

Generic cache invalidation must discard computed/seeded heights, not the current content owner's only asynchronous size observation. A real content configuration change must discard the old observation and receive a newly generation-qualified GeometryReader observation even when its CGSize is unchanged.

Success: unchanged-content clear retains real height96; same-size configuration replacement reacquires legacy observation160 (a cached160 frame alone does not qualify); invalidated seed176 does not override fresh120. Initial sizing96, genuine growth160 and fresh120 recovery controls must remain green.

Independent failure signals: missing visible fixture rows, nonfinite/missing frames, failed control, mismatched nonce/runtime, old-owner sample relabelled for a new owner, stale seed overriding an observation, a cached frame without a new measurement, or introduction of synchronous measurement on the legacy path. A failed build/launch/control is invalid evidence, not a reproduced sizing failure.

## Native evidence and ablations

Run `35596858464`, source `8b60127`, pinned Xcode26.2/build17C52, iOS26.2 simulator with only the legacy routing branch forced:

- Baseline: generic clear leaves cell40/host96/legacyNil; same-size replacement leaves legacyNil despite frame160; stale seed176 overrides host120.
- C1 (cache policy only): clear96/96 and seed120/120 recover, but same-size replacement still has legacyNil.
- C3 (C1 plus generation-qualified preference): clear96/96/legacy96; same-size replacement160/160/legacy160; seed120/120. All initial/recovery controls pass.

Evidence directory in the owner workspace: `reports/openminis-ios15/revision-run-35596858464-8b60127-66CHO9/`. The native fixture is not an iOS15 device or full-app visual acceptance.

## Production implementation

`SelfSizingCell.clearCachedHeight` preserves same-owner `legacyMeasuredSize`, clears computed height/width/media-time and stale seed height/width. Real configuration replacement and reuse still clear owner-specific state.

`LegacyHostedSize` carries configuration generation and the real `proxy.size`. A changed generation makes a same-size preference distinct. The producer guard rejects a superseded generation before lastSize mutation/dedup. Existing asynchronous coalescing and scheduled-callback ownership checks remain unchanged. The obsolete CGSize-only entry is removed; only test copies widen access to the new entry.

The current legacy source is byte-identical to the natively tested C3; cache clear differs only in explanatory comments. Source-contract tests enforce both.

## Test ownership and frozen baseline

Both comparison generators read pre-fix source from the immutable commit above, not from the repaired worktree. Their workflows fetch history so the pinned object exists in fresh Actions checkouts. This avoids a false-green baseline after the production repair.

The notification-contract fixture uses a marked read-only generation getter and widened payload/method visibility in a generated copy. Restoring those edits recovers the exact production source. No test-only accessor enters the App.

## Delivery gates / resume

Local gates: revision26, observation22, compatibility18, retry4, size-delivery13; Python/Bash/YAML checks and diff checks. Native follow-up: run the size-delivery contract against the repaired commit and run the existing full `ios15-m0-baseline` workflow on that same commit. Preserve logs and IPA manifest; verify SDK pins, source SHA, minimum OS, forbidden diagnostics, artifact SHA and package contents before delivery.

Do not call native experiment success a delivered visual repair. Full-app build and the user's iOS15 acceptance remain separate gates. No further repeated capture of the already-established c595b222 episode is needed.
