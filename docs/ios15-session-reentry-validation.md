# iOS 15 single-session re-entry: correction and acceptance contract

Status: diagnostic contract, not a new production fix. Phase 1 remains open until the actual failure has an executable native reproduction. This replaces the rejected general scroll-anchor proposal.

## 1. Locked baseline and scope

- Diagnostic branch: `diagnostics/ios15-session-reentry`, based on `ef9decf8ca231bcfd846a731eaad02a646b8a701` from `fix/ios15-session-width-contract`.
- Preserve the existing `ef9decf` width fix and `190c25e` legacy-height-delivery fix.
- Original user goal: one particular long conversation visibly shrinks/jumps on re-entry. Do not redefine this as remembering every conversation's reading position on every OS.
- Normal first-entry positioning, explicit send/retry/jump-to-bottom actions and existing iOS 16+ behavior remain unchanged unless an isolated reproduction proves that one of them violates this goal.
- No navigation rewrite, new persistent scroll state, arbitrary delay, or extra width clamp without evidence that it owns the reproduced failure.

The source hashes in the rejected-candidate archive were verified before retirement. All four affected Swift files were restored with targeted edits, and byte comparison against `git show HEAD:<path>` passed for each. The rejected test was moved out of the active `scripts/` directory without deleting it. Its old green result must not be used as a gate.

## 2. Evidence already available

The private export and the two candidate Markdown replies (1303/1447 characters) already exist under the workspace's `reports/openminis-ios15/session-export-b44d9dca/`. The user does not need to resend the conversation.

Historical evidence at the pre-width-fix baseline: the row-height shrink/recovery included a real collection-content-height change of 381 points held across about 1.482 seconds. That is stronger than a pair of temporary measurement requests, but it does not identify the first invalid-height producer.

The latest log contains bounded main-text measurement widths and stable observed browse-settle geometry, plus cached re-entry force-scroll events. It does NOT by itself prove that the old height regression still exists at `ef9decf`, nor that force-scroll is the cause of the user's visual complaint.

The earlier native driver in `worktrees/openminis-session-width/scripts/ios15-session-width-probe/` answers only an intrinsic-width question:

- Its `ProbeTextView` reproduces a size contract but is not the full production `SelectableMarkdownTextView`.
- It uses real legacy hosting and extracted code attachments, not the complete production parser/cell/layout chain.
- Its `shrink` phase deliberately changes text and physical width; that is not unchanged-content session re-entry.
- Its sampled `bounded` verdict is not an actual per-frame viewport/row-height assertion.

Therefore do not launch that same driver and rename a green result a fix for this remaining problem.

## 3. The native feedback loop to build

Prefer the app's existing DEBUG UI-test route (`--uitest-message-list`), `MessageListTestView`, `SessionDataSimulator`, and actual `CollectionViewMessageListV3`/`SelfSizingCell`/legacy-hosting path. Extend a controlled re-entry scenario through the REAL parser, text view and layout instead of creating another fake view that merely contains similarly named methods. The current built-in scenarios exercise rendering/scrolling/streaming but do not implement this controlled re-entry contract.

Keep the following observations in the same run and case identity:

1. Input/content hash, viewport width, run ID, source SHA, mount/configuration generation.
2. Live text bounds/container width, rendered content height and whether the sample is a provisional probe or a valid-width result.
3. Hosted size report, cell accepted/preferred height and which cache/estimate source supplied it.
4. Actual row frame, collection content height, viewport-relative item position and scroll offset.
5. User interaction, explicit scroll request, inset/viewport changes, and the owning session/mount for those events.

Do not log message bodies or copy the private export into a public repository/Actions artifact. Structural neutral fixtures are already prepared, but their typography/heights are not proven equivalent to the original. A neutral-fixture pass cannot settle device acceptance without the original in-app conversation check.

### Cases

- Cold entry and repeated unchanged-content re-entry, with a finite fixed viewport.
- Re-entry followed by no user action: distinguish legitimate initial placement from later unsolicited content displacement.
- Browse, drag/deceleration and settle, including a delayed real height report. Separate finger-driven offset motion from a content frame moving under a stationary viewport.
- Newer mount/configuration followed by an old completion. Stale work must not change the current page.
- Plain-text and stable finite-width controls.
- Width change, async attachment completion and explicit bottom-jump/send as regression controls. Legitimate changes must still work; hiding the row or suppressing all callbacks is not success.

### Oracle and result classes

- `BUG_REPRODUCED`: the actual native baseline produces the reported same-content transient under-height/recovery or unrequested visible displacement, with correlated owner/geometry evidence and intact controls.
- `CANDIDATE_PASSES`: only after baseline is red-capable, a minimal candidate removes that specific failure and preserves all controls.
- `INCONCLUSIVE`: baseline did not reproduce, or only the related width-probe failure was observed. This is not a green device fix.
- `INVALID_RUN`: build/runtime/report/provenance/control failure. Compiler errors and missing/zero-sized content are not bug-red or success.

Expected terminal geometry must come from an independent real finite-width render/reference, not from the same cache being tested and not hardcoded historical device heights. Missing evidence is never treated as zero displacement.

Test the oracle itself: disable the actual correction/restoration/owner gate implicated by a reproduction and require the corresponding behavioral assertion to fail. Source-string assertions may be supplemental lint only. The rejected test passed with both capture and restore bodies empty and is retired for that reason.

## 4. Implementation gate

Only after the failure is reproduced, trace the first incorrect authoritative write. Change that owner, one variable at a time, then rerun the same native sequence. Do not preselect navigation, global read-position storage, measurement clamping or cache eviction merely because those calls appear near the event.

Before delivery: syntax/type-check/full build, native red/green and regression controls, artifact/source provenance, then one iOS 15 device acceptance using the existing conversation. A forced legacy path on iOS 26.2 is not iOS 15 device acceptance.

## 5. Current completed work and blocker

Completed:
- Rejected WIP and test fully archived with hashes at workspace `reports/openminis-ios15/reentry-contract-review/rejected-candidate/`.
- Production source restored exactly to `ef9decf`; width/compatibility checks pass 19/19. These checks verify retirement did not alter the accepted source baseline, NOT that the original visual bug is fixed.
- Existing native probe's coverage gap inspected and documented.

The user has authorized the diagnostic branch commit, push and cloud test. No production package or private conversation upload is authorized by that approval.

Native execution is still blocked, not merely waiting for permission: this Linux host has no Apple runtime, and the current full-App simulator path cannot link the device-only `libish_emu.a`. See `BUILDING.md` (simulator dependency warning) and `deps/build_rclone_ios.sh:8–11` (explicitly identifies the iSH blocker). Selecting the cloud macOS runner alone does not remove that ABI limitation. Do not launch a knowingly invalid full-App simulator job or silently replace the actual renderer with the old width micro-probe.

Before an unattended Simulator re-entry run can be truthfully dispatched, provide either a working simulator-native dependency set or a standalone native target retaining the real rendering/measurement/cell/layout chain. Non-rendering services may only be isolated behind explicit fail-fast test boundaries; replacing actual measurements with fakes or hardcoded heights does not satisfy this contract. Any standalone target must state which full-App lifecycle portions it does not cover. Keep Xcode 26.2/17C52 pinned, and keep actual iOS 15 acceptance separate.

## 6. Practical continuation: instrument the real device build

The user explicitly asked to continue toward compilation. The full device build already works and uses the exact real parser, renderer, text view, hosting, cell, layout and navigation. The Simulator-specific iSH limitation must not be treated as a blocker for this device path.

The diagnostic branch now adds bounded, DEBUG-only scalar observation blocks and an opt-in IPA stamp. The blocks do not change layout-return values, height caches, scroll state or navigation; removing them restores the nonblank source to `1f47275`. Recordings have source SHA, run/sequence, generation and anonymous ownership links, plus actual measured/returned heights and viewport events. No additional measure/layout invalidation is performed. A dedicated `reentry-<run UUID>.log` appears in the existing log browser, so the user can return the exact trace rather than export the conversation again.

Workflow `.github/workflows/ios15-reentry-device.yml` uses the existing pinned `generic/platform=iOS` dependency/build path, not the blocked Simulator target. Before packaging it runs actual native Foundation tests of the extracted production collector and a compiled no-op mutation that must fail the expected runtime assertion. These tests verify the collector, NOT the original UIKit symptom. Full App compilation/link and opt-in signed IPA packaging follow. Packaging defaults stay off and remove inherited diagnostic activation from ordinary repacks.

This is a diagnostic IPA, never a claimed fix or public release. Native re-entry acceptance remains human-in-the-loop on the original phone and conversation. Follow `scripts/reentry-diagnostics/README.md` and `hardware_loop.sh`; the returned telemetry must be interpreted with the user's visual feedback. The analyzer's CAPTURED means correlated telemetry, not bug-free UI. Compiler failure, missing capture, or a cap/nonfinite marker must never become a green fix result.
