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
- A small pure `InputPromptLifecycle` owns opening, requested completion, external cancellation and one-shot dismissal consumption. The caller presentation binding remains true until after submit/cancel callback. This prevents `folderToRename` from being cleared before submit and runs duplicate-name follow-up only after sheet dismissal.
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

Implementation follows the independent source audit's legacy Form-sheet recommendation. Original fields/messages, validation and persistence handlers are retained. A second read-only implementation review has been requested from the same visible child session; it is not yet a completed review.

Request changes are diagnostics only: Responses builder logs requested level; the final outgoing-body logger prints bounded `reasoning.effort` and `reasoning_effort` metadata before the truncated body preview, now explicitly labelled a preview. The request serialization and policy are unchanged. Synthetic tests cover truncation, missing/null/nonstandard values, no body mutation, and no prompt/unknown-value leakage.

Device screenshot remains the original failing runtime evidence. No iOS 15 simulator/device is attached here. Apple type checks and the eight-stage forced-legacy/native-modern component probe are mandatory cloud gates before packaging. Thinking wire mismatch remains unproven pending a complete request, not declared repaired.
