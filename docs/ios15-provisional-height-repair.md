# Provisional-height repair: restart from a verified baseline

Status: full-source device baseline test App prepared for cloud compilation. No production fix or native runtime pass is claimed.

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

The next deliverable is a runnable native reproduction/test harness, not another readiness implementation. Use real production rendering/measurement and real legacy host/cell/layout implementations. A neutral deterministic input is permitted; synthetic heights or substitute measurement views are not. Non-rendering app services may only be isolated at explicit fail-fast boundaries, with those limitations listed.

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
