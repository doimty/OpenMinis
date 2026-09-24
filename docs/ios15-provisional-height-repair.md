# Provisional-height repair: restart from a verified baseline

Status (2026-09-24): the full production-list device capture 5c8f2419 is CAPTURE_VALID and proves committed transient shrink/recovery. The scoped layout-policy candidate below is ready for implementation; neither a production repair nor candidate device acceptance is claimed.

## Baseline and scope

- Active branch: `fix/ios15-provisional-height`.
- Worktree: workspace `worktrees/openminis-provisional-height-fix`.
- Source baseline: `2f21e242df71d63576682f8ac61c50a097f3a5ae`, whose full device build and diagnostic IPA were verified.
- Rejected candidate `272656b9ed103ce601ebaeaca39267652bbaee7f` remains intact in the prior diagnostic branch/worktree. It is not the new implementation baseline.
- Preserve the accepted `ef9decf` intrinsic-width and `190c25e` delivery/generation fixes. No navigation or generic scroll-position feature.
- Current source-isolation tests pass 4/4. Keep those checks for the observation-only baseline; a future functional candidate must have a separate, explicitly scoped validation path, not a disabled or self-comparing isolation gate.

## Known evidence and open boundary

The original device trace contains two ownership-correlated local under-height writes/recoveries. The user confirmed visible jumping on the diagnostic build. The event cap still prevents declaring complete, timestamp-correlated original re-entry acceptance. Existing private conversation exports remain local; do not request them again or upload them publicly.

The rejected candidate confused a new preference revision with a newly valid geometry value and had observer/lifecycle gaps. Its three source-string tests remained green with the gate, notification or finite-width token disabled. Do not carry those checks forward as a behavioral oracle.

## Hypothesis to make executable

A provisional height produced before the real finite-width text layout is established can be promoted through the real legacy host/cell/layout path and replace the precomputed row height. A correct finite measurement being available does not prove that the delivered host geometry uses it.

The original next-deliverable gate was a runnable native baseline harness, not another readiness implementation. That gate is now met for the committed transient geometry regression by the real production-list capture described below. Retain real production rendering/measurement and real legacy host/cell/layout implementations for candidate acceptance. Synthetic heights or substitute measurement views are not native reproduction evidence. Non-rendering app services may only be isolated at explicit fail-fast boundaries, with those limitations listed.

## Required observations and verdicts

- Immutable source SHA, generated-source hashes, case/nonce, runtime and viewport width.
- Actual text bounds/container width, intrinsic/finite measurements, host reports, cell returns, authoritative cache/frame writes and user/test-driven lifecycle events.
- Reference height from an independent real finite-width render of the same fixture, not from the cache under test and not historical constants such as 1484/1865.
- Repeat mount/reuse at unchanged content and width. The test may explicitly drive the existing deferred-layout policy to reproduce the relevant local state, but must label that as policy-driven, not a real finger/deceleration or complete application-navigation test.
- `BASELINE_REPRODUCED`: the actual native baseline exhibits the local provisional under-height admission, with nonempty visible content and intact controls.
- `INCONCLUSIVE`: the baseline does not reproduce; do not call this green.
- `INVALID`: source extraction, compile, launch, identity, empty/missing geometry or control failure; never bug-red.
- A later candidate may pass only by removing the captured erroneous write while preserving finite valid growth/shrink, multiple renderers, empty/plain content, width/content changes and generation/observer handoff controls.

Disabling the actual candidate admission/notification owner must fail a native behavioral assertion. `build-for-testing`, source-string checks and replay of historical logs do not satisfy that requirement.

## Execution and evidence plan

1. Implement the baseline driver inside a full App Debug build, with complete production classes rather than standalone source slices. Driver, source overlay and validator live in `scripts/ios15-provisional-height-probe/`.
2. Use `ios15-provisional-height-device.yml`, pinned Xcode 26.2 / 17C52 and the established full device dependency path. The former Simulator draft failed review and is archived outside the repo; none of its stub/sliced classes or settled-snapshot-only verdict is used.
3. Verify complete production-source hashes and exact inverse restoration of the temporary entry/read-only overlay. Package an independent `com.openminis.layoutprobe` test App, without normal Minis URL, app-group, keychain or extension registration. Compilation is not native execution; the test App must run on the actual iOS15 device.
4. Choose a minimal production change only after the relevant native failure is executable. Keep the producer/result/consumer identity explicit; adding a mutable revision number by itself is not the solution.
5. Build/package/source verification and original-phone acceptance follow later. No package delivery is authorized by a mere harness pass.

## Current ownership / resume

Main agent owns integration, commit, cloud validation and delivery. The current native driver and reversible overlay were implemented by the main agent. Delegated report validation was corrected against actual production event shapes and strengthened with fail-before/pass-after checks for missing delivery/configuration/provisional text, cross-row false pairing, dropped records and backwards clocks. The previous worktree and failure evidence stay untouched. Read `scripts/ios15-provisional-height-probe/README.md` for the exact build/runtime/result boundaries.

## 2026-09-24 implementation decision: precalculated-height admission and pending-value freshness

### Baseline read set and evidence

- Candidate starts at delivered source `b47cb87a33b4991c50396f727e39fb99387bfa60`; production `src` remains byte-identical to `2f21e24`. Preserve the three existing uncommitted offline-validator/progress changes.
- Source of truth: `MessageListLayout.shouldInvalidateLayout`, `invalidationContext`, `applyDeferredHeights`, width/reset/remap paths; `SelfSizingCell` legacy delivery and cached-height path; production Coordinator's settle sequence. Layout owns admission and pending-height application. Do not add a competing host readiness/revision owner or bypass the accepted generation guards.
- External evidence: `reports/openminis-ios15/replay-report-5c8f2419/{report.json,verdict.json,findings.json,witness-events.json,verify_witnesses.py}`. Capture has6144 events/2 gestures/1 reentry, exact source/input pins, unchanged width. Row238 commits1631→1376→1631 over26.388ms and contentSize25399→25144→25399; row231 commits1298→1203→1298 over28.222ms and contentSize25399→25304→25399. Full delivery/acceptance/frame chain is generation-correct.
- `phase=pre` is recorded after the cache write: the prior source was `precalcHeights`, while the deferral guard currently only protects `heightCache`. A row with a precomputed height therefore passes the first-measurement exception while browsing. This is the concrete candidate admission seam, not evidence of a stale-generation callback.

### Chosen narrow candidate

1. While deferring self-sizing, protect an existing valid precalculated height as well as an existing measured cache entry from downward replacement. Keep coarse estimates separate: a genuinely unmeasured row must still be able to acquire its first size. Continue allowing growth to avoid truncating text/attachments.
2. Make the pending shrink entry reflect the latest observation for that same row state. A later stable/restored height cancels the earlier shrink; an accepted growth must not leave an older shrink queued. Otherwise merely blocking the immediate write moves the same bad value to `applyDeferredHeights` at settle.
3. Preserve legitimate shrink on thaw, including zero-height content/footer cases. Source inspection confirms that `scheduleWidthPurge`, `updateCacheForSnapshot`, `invalidateHeight(at:)` and `clearHeightCache` currently clear/remap other height state but leave `deferredHeights` untouched. Pending values therefore need the same lifetime boundaries: discard at width/session reset and explicit content-height invalidation; remap surviving unchanged items with the snapshot identity mapping and discard removed/replaced state. Audit content-key replacement as part of this boundary rather than assuming an unchanged index means unchanged content. Keep this ownership in `MessageListLayout`, not a second host-level revision system.
4. Do not make heights globally monotonic, seed precalc into the measured cache as if it were authoritative, add synchronous iOS15 measurement, clip overflowing text, or restore/rewrite contentOffset as a substitute for correct height admission. Preserve the existing renderers, host delivery/generation guards, row spacing and iOS16 hosting route.

### Test-first evidence plan

- Production implementation is not yet changed. First create a behavioral regression at the actual admission/pending-state seam. Old code must admit the captured lower preferred height with precalc present and no measured cache; the candidate must defer it. The same actual policy code must be used by the production caller and any extracted Foundation test, not a parallel Python implementation presented as UIKit evidence.
- Required controls: measured-cache shrink; precalc-only shrink; unknown/coarse first measurement; immediate growth; genuine shrink applied on thaw; shrink→stable cancellation; shrink→growth cancellation; two distinct rows; zero/empty row; width change; content replacement and index remap. Last three guard against applying another state owner's queued height.
- Candidate native acceptance uses the unchanged real-list input and production rendering path, not historical trace replay as a claim that new UIKit behavior passed. The existing trace audit is an immutable baseline witness only.
- Native success: neither captured provisional low value becomes a real frame/contentSize reduction while browsing; valid growth and legitimate shrink controls still work; no old queued low value appears at settle. Independent failures: stale pending write, clipped growth, permanently tall genuine shrink, cross-row/width contamination, missing producer/control/settled evidence, source gate mismatch, or a cap/launch/build failure.
- Ablations: restoring the old precalc-blind admission must bring back the immediate bad write; removing pending-value cancellation must resurrect an obsolete shrink on thaw. Each mutation must compile before its expected behavioral failure counts.
- Do not weaken the existing observation-only source-isolation gate. A functional candidate needs a separate explicit allowlist for the intended production diff, immutable baseline comparison and unchanged other-source hashes. Retain pinned Xcode26.2/17C52/SDK26.2 and artifact verification. Compilation/policy tests alone cannot be reported as the original phone's visual repair.

### Remaining boundary and resume

The supported target is the committed transient height regression. A1482ms original visual gap and final settlement after the last captured gesture are not proved by5c8f. This boundary does not require the user to keep repeating equivalent baseline captures. Proceed to the candidate implementation and tests, then the existing cloud/device delivery gates. No new permission or input file is needed for the already-authorized project scope.

## Execution checkpoint: user authorized repair on 2026-09-24

- Frozen starting HEAD is `b47cb87a33b4991c50396f727e39fb99387bfa60`; the existing four documentation/offline-validator edits are preserved. No production file has changed at the first test checkpoint.
- Agreed seam: the actual layout's public admission/invalidation, deferred-flush and cache-lifecycle APIs. `scripts/ios15-height-policy/` compiles the **entire** production `MessageListLayout.swift`, changing only its UIKit import to Foundation and supplying explicit non-rendering platform adapters. The real `MessageListItem` enum is copied byte-for-byte from its source. No production method is sliced, substituted or rewritten. This isolates actual Swift policy, not UIKit scheduling/measurement/rendering; device replay is still mandatory.
- First vertical slice asserts the captured precalc-only 1631→1376 admission is rejected while deferring, with unknown-first-size, growth and idle-shrink controls. Pinned Xcode26.2 must compile the baseline and show the exact expected behavioral failure before the production gate is changed. A compiler/process failure is INVALID, not bug-red.
- Subsequent slices cover cancellation and lifecycle, then a separate candidate source/build profile. The original observation-only validator and baseline overlay stay intact. A reviewed production allowlist/hash manifest will compare the candidate to immutable2f21e24; the baseline must never be silently changed to HEAD.
- Review uses the existing independent evidence-review session because new-subagent spawning is currently blocked by the exposed `streamTo` schema. Do not describe the reused reviewer as fresh context.
