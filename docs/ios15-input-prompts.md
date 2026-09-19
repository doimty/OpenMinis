# iOS 15 text-input prompts and request diagnostics

## Locked baseline / user evidence

- Baseline `060a12a8ed12259a0efc8d82ebb1bcaaf86c4492` (documentation on product source `31981cc`); clean worktree checked before branch `fix/ios15-input-prompts`.
- Screenshot SHA256 `3c93fa7034449ab77cf96c70a4d719e44e4c5e54cbd867a7776be9bcabd200ff`: New Group title/message/Cancel/Create, but no editable name field. `ModelGroupsView` resets the draft to empty and its existing create handler correctly refuses that empty value.
- Log SHA256 `330e40333d7d9cf17e0d2dd69740a782959de95bfcb8854345b4c3dbefbff9e7` is an exact prefix of the larger `fab0fd73169dfac6a15b99e3b5c6b73cb296dc837f9a70cd3ef88e6483942c01`; do not count these as two independent request traces. Raw private files remain outside this repo.
- The larger log records `gpt-5.6-luna` via Responses API, declared tiers including xhigh/max, a main response and a separate title-generation request with thinking off. Its main body is 47,660 characters, but `logOutgoingRequest` prints only the first 3,000. Missing effort in that truncated preview is not proof of a missing wire parameter. Full captured requests were requested through the existing Copy Requests menu. No reasoning-policy change is justified yet.

## Baseline read set and independent audit

`ModelGroupsView`, `FolderAlertsModifier` in ContentView, RcloneAddServerView, BackupDestinationDetailView, CloudSyncSettingsV2View; SwiftUICompatibility/AppLocalization; OpenAIAgentProvider Responses builder and reasoningEffort; OpenAIProvider streamRaw/logOutgoingRequest; AIChatViewModel thinking state and Copy Requests action.

An existing visible child session performed a read-only source audit, saved outside the repository as `reports/openminis-ios15/input-alert-audit.md`. It found 7 input-alert sites / 8 fields: six flows reachable on iOS 15, plus Device Name behind an iOS 17 route. There are no SecureField alerts. Its recommendation is adopted: a small legacy sheet/Form, not a new UIKit presenter/window lookup. No background agent is authorized to modify the repo for this task.

## Hypotheses / predictions

1. iOS 15 omits TextField children of SwiftUI alert actions. Prediction: moving those same fields to a standard Form in a legacy sheet makes real editable controls available; the existing validation/store code remains untouched. This matches the screenshot, not an input-color guess.
2. A naive adapter clears an optional presentation target too early or presents a collision alert before the sheet disappears. Prediction: an explicit dismissal-completion state machine, holding the owner's binding until after the callback, preserves the rename target and permits its follow-up alert.
3. Thinking mismatch could be UI state/clamping, request emission, or upstream interpretation. The provided preview cannot distinguish these. Add bounded, content-free final-body effort metadata to request diagnostics only if needed; do not change effort values based on token counts or title-generation behavior.

## Design / scope

- One `compatTextInputAlert` adapter. iOS 16+ retains native SwiftUI alert; iOS 15 uses NavigationView + Form + explicit Cancel/confirm controls in its own sheet.
- Pass title/confirm labels as SwiftUI Text and retain the original field/message builders. This preserves in-app localization, interpolated messages, field bindings and keyboard modifiers; do not flatten them through String localization.
- A small pure `InputPromptLifecycle` owns active/closing/completion state. The sheet's requested visibility is derived directly from the caller binding, not mirrored through an onChange observer. The caller presentation binding remains true until after submit/cancel callback; its actual value is rechecked at dismissal to veto a revoked save. This preserves `folderToRename` for submit and runs collision follow-up after sheet dismissal.
- No universal empty-input rule, new folder-name policy, parent environment dismiss, configuration/storage/schema changes, or capability-gate removal. Device Name can remain empty to reset its default. Rclone Cancel/validation/path logic stays at its current owner.
- Migrate all seven input builders to the adapter; Device Name is preventive source consistency, not a claim of a seventh currently exposed iOS 15 failure. Button/message-only alerts remain native.

## Verification plan / independent failure signals

- Red-capable source gate: raw alert actions containing TextField/SecureField fail. Include comments/strings, multiline, presenting/item forms and mixed one/two-field fixtures. Gate the adapter's native branch at iOS 16.
- Pure production lifecycle tests: confirm/cancel exactly once, swipe as cancel, external false as no action, no reopening during dismissal, repeated open/close; callback executes before owner clearing and only after dismissal.
- Native forced-legacy component probe on pinned iOS 26.2: actual sheet contains editable one/two-field controls; CJK edits update bindings; confirmed values/optional target survive dismissal; follow-up presentation and outer-sheet preservation. This is the production legacy component on a newer runtime, **not an iOS 15 runtime or a red reproduction of Apple's old alert behavior**.
- Full build and existing symbol/compatibility/IPA gates; verify source SHA, nonempty Apple logs and packaged minimum OS/hash.
- Failure signals independent of field visibility: duplicate writes, a nil rename subject, lost description, accidental save on cancel/swipe, parent browser dismissal, a lost follow-up collision alert, localization flattening, or old supported alerts changing unnecessarily.

## Checkpoint

The raw-alert source gate was executed before implementation and failed on all 7 input builders. After migrating them it passes (6 parser/contract tests); the existing symbol suite 10/10 and compatibility suite 17/17 also pass. Shell/embedded Python/YAML syntax and new Swift syntax-tree parsing were checked; these are not Apple type checks.

Implementation follows the independent source audit's legacy Form-sheet recommendation. Original fields/messages, validation and persistence handlers are retained. The second read-only implementation review identified two P2 gaps: checking the current owner at completion rather than trusting observer timing, and testing immediate collision-action reopening plus modern multi-field extraction. Both are incorporated into the revision described below.

Request changes are diagnostics only: Responses builder logs requested level; the final outgoing-body logger prints bounded `reasoning.effort` and `reasoning_effort` metadata before the truncated body preview, now explicitly labelled a preview. The request serialization and policy are unchanged. Synthetic tests cover truncation, missing/null/nonstandard values, no body mutation, and no prompt/unknown-value leakage.

Device screenshot remains the original failing runtime evidence. No iOS 15 simulator/device is attached here. Apple type checks and the eight-stage forced-legacy/native-modern component probe are mandatory cloud gates before packaging. Thinking wire mismatch remains unproven pending a complete request, not declared repaired.

### First native probe and corrective revision

Run `35435115684`, source `6f0841f32ceb1e9d9d91911f2635575d0326ca3a`, passed iOS15/16 type checks, 38 lifecycle checks, and request-metadata tests. The actual legacy component displayed an editable CJK field and passed first confirm/cancel, but then failed to reopen (`timeout: external-cancel field`). The failure screenshot showed only the underlying view; **no package was built or delivered**. Evidence: workspace `reports/openminis-ios15/input-prompts/run-35435115684-wYILZw/`.

The first adapter mirrored the owner flag through onChange into another presentation boolean. A rapid false/true owner cycle can be coalesced, leaving that mirror false. The corrective design removes the mirror/observer entirely: derive sheet visibility directly from the owner while pending completion suppresses reopening during dismissal. Native appearance records activity, and dismissal rechecks the current owner even if no observer ran. New pure cases cover reopening without an observer edge and revocation without prior synchronization. The next native run must validate the correction; source reasoning alone is not acceptance.

The probe now invokes the same Change Name state action while the collision alert is still up (no artificial dismissal wait), checks a modern two-field builder with modifiers, and records owner/activity/pending/field counts on failure. Close/action seams remain programmatic, not XCUITest taps.

## Final evidence

Corrected source `889af8d99006a6a65a6da3a2424ac8d25f14e3b1`, run `35435999025`: success. All 8 native phases passed, including the previously failing cancel/reopen sequence, immediate collision-action reopening, nested flow retention and modern two-field extraction. Production lifecycle: 61 checks; native call-shapes: iOS15.0/16.0; full app compile/package passed. Independent delta review of the two ownership files found no new P1/P2. Device iOS15 acceptance remains pending.

IPA delivered as `Minis-1.13-ios15-input-fix-889af8d.ipa`, 84,226,993 bytes, SHA256 `936bf1d041327dfd7395b0c4299a7967c3de10c668f5799cd9cebdf8833aa40d`. Actual bundle/binary minimum is 15.0/15.0.0, SDK26.2.0; all new helper markers and the earlier SF Symbols helper are present; test resources are not bundled. Verification and logs: workspace `reports/openminis-ios15/run-35435999025-889af8d-eIwy59/`.

This delivery does not claim an effort-policy fix. The later cumulative log still contains truncated request previews, so the user was asked to reproduce once with the new bounded metadata or provide Copy Requests. A separate, imprecise chat-screen click report is also not accepted as fixed by this input-prompt patch.
