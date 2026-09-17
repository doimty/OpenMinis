# iOS 15 compatibility port

## Status and authorization

- User authorized starting implementation on 2026-09-17, then 20:46/21:02 asked to proceed and questioned the missing fork.
- Work branch: `compat/ios15`.
- Upstream baseline: `main`, commit `4ef29002e88db1e20e462ec2ff46916e8a7dcb45`, app version 1.13.
- Fork target: `doimty/OpenMinis`. Do not push to `OpenMinis/OpenMinis` or to `main`.
- M0 CI: `.github/workflows/ios15-m0-baseline.yml` on `macos-26`, Xcode **26.2** (`17C52`), iOS SDK **26.2**. This matches the project's 26.2 test/SDK pin; it is not the runner default 26.6 and not the unrelated Theos 15.4 pin.
- Local host has no Xcode/Swift compiler. Keep local changes small; do not download SDKs or native artifacts here.

## Product contract

Build an iOS 15-compatible core edition while preserving the upstream iOS 16+ behavior where practical.

Core acceptance scope:

1. Launch on iOS 15, configure a provider and send a message.
2. Render streaming text and tool results correctly, including long conversations, images, keyboard transitions, scrolling and cell reuse.
3. Execute an actual command inside the bundled Linux sandbox; assert output and exit status, not merely that the sandbox starts.
4. Persist and restore conversations in the existing SQLite format.
5. Preserve ordinary file operations, browser access and attachment import.

The first iOS 15 milestone does not promise Live Activities/Dynamic Island, AlarmKit, current App Intents actions, current replicated File Provider integration or the CKSyncEngine-based sync backend. They must be unavailable explicitly on iOS 15, not crash at launch, and must not silently disappear on already-supported systems. Ordinary notifications, local storage, backup/import/export and internal file access are separate from those integrations.

No upstream API-key customization values, signing secrets, local user data or parent-workspace files may be committed.

## Baseline read set and ownership

| Area | Source of truth | Owner / boundary |
|---|---|---|
| App and extension build configuration | `src/ios/Minis.xcodeproj/project.pbxproj`, `BUILDING.md` | Build configuration; main app currently 16.0 / Swift 5.0, tests 26.2 / Swift 6.0, widget 16.2 |
| Navigation | `src/ios/Views/ContentView.swift`, `src/ios/MinisApp.swift` | Session routing and view lifecycle; preserve route identities and deep links |
| Message hosting | `src/ios/Agent/MessageList/CollectionViewMessageListV3.swift`, `MessageListInfrastructure.swift`, `MessageListLayout.swift` | SelfSizingCell owns configuration lifetime, size cache and generation invalidation |
| Media and layout | `src/ios/Views/Chat/ChatInputBar.swift`, `AIChatView.swift`, `Views/Settings/SoulSettingsView.swift` | Attachment selection/transfer and wrapping layout |
| Persistence/providers | `src/ios/Agent/Chat/ChatStore.swift`, `src/ios/Providers` | Existing SQLite schema and wire contracts stay unchanged |
| System integrations | `src/ios/Agent/Intents`, `Agent/Background`, `Agent/Sync`, `FileProvider`, `AgentWidget`, `NativeOffloads/AlarmOffloadBridge.swift` | Feature availability must be consistent across UI, entrypoints and linked extensions |
| Native build | `deps/build_ish.sh`, `build_ffmpeg.sh`, `build_lame.sh`, `prepare_alpine_rootfs.sh` | Pin dependency commits/versions and verify output ABI/deployment metadata |
| Tests | Existing `src/ios/MinisTests` / `MinisUITests` plus narrow compatibility tests | Source inspection and syntax checks do not substitute for Xcode or device execution |

Supporting assessment (not a build proof): `/root/.openclaw/workspace/reports/openminis-ios15/assessment.md`.

## Hypotheses and independent failure signals

### H1: Core engine is portable without a data-model rewrite

Evidence motivating H1: SQLite rather than SwiftData, ObservableObject rather than Observation, native scripts targeting iOS 14, checked SwiftAnthropic/VAD manifests permitting iOS 15.

Success: model request/tool round-trip and SQLite history restoration work on iOS 15 without a schema fork.

Independent failure signals: a dependency binary requires >15; unresolved weak symbols at launch; sandbox guest command cannot execute; conversation content changes or is lost across launch/relaunch.

### H2: A narrow compatibility layer can preserve existing navigation semantics

Success: new/continued chat, direct deep link, notification-open, back navigation and session-switch all reach the intended session on iPhone; native iOS 16+ path remains behaviorally unchanged.

Independent failure signals: reused view model points at a stale session; back stack duplicates entries; a background task publishes into a destroyed navigation subtree; existing iPad split navigation regresses.

Ablation: native iOS 16+ route must continue using the current implementation. Exercise the same explicit route transitions through the legacy implementation, not an unrelated simplified demo screen.

### H3: Legacy hosting preserves the message-list invariants

Success: iOS 15 uses a UIHostingController-based host for the four current UIHostingConfiguration branches, retaining configuration invalidation, reuse cleanup and correct height reporting. Native iOS 16+ continues to use UIHostingConfiguration.

Independent failure signals: growth of active host/controller count after repeated reuse; missing EnvironmentObject; stale height after streamed content; duplicate child-controller attachment; scroll-position jumps or overlap while streaming.

Ablation: compare native and legacy hosts with identical message fixtures and update sequences. An empty/static text cell is not sufficient evidence.

## Milestones and gates

### M0 — Reproducible upstream build (current gate)

- Provision a macOS CI job after user approval of commit/push/fork operations.
- Select and pin an actually available Xcode + SDK version supporting the source's newer gated APIs; do not blindly reuse an unrelated Theos/Xcode 15.4 baseline or follow `latest`.
- Build the unchanged baseline before applying compatibility changes.
- Pin iSH submodule `3f6384c70eefd1a370f121d3492a5f21f7767df9`, resolve and retain SPM versions; inspect the VAD binary target minimum OS.
- Record Xcode/SDK/tool versions, dependency versions, commit, nonempty logs, artifact SHA256 and the baseline result. Do not infer build success from a workflow starting.

### M1 — Minimum deployable iOS 15 target

- Set deployment target only after establishing baseline diagnostics.
- Define explicit runtime/build boundaries for App Intents, replicated File Provider and widget components. Check every registration/caller as well as implementation declarations.
- Keep newer-framework imports/links weak or excluded as appropriate; absence on iOS 15 must be safe.
- Compile to enumerate remaining availability errors, then fix by actual diagnostic, not global blind replacement.

### M2 — Core vertical slice

- Navigation compatibility first, then legacy message hosting and attachment/layout adapters.
- Keep new compatibility modules narrow. Avoid shadowing Apple type names globally; do not globally redefine NavigationStack or UIHostingConfiguration.
- Preserve old/new ownership explicitly: platform-native path retained for iOS 16+, legacy path only selected when needed.
- Run compiler, model/route tests and the same chat fixtures in both paths.

### M3 — Runtime verification and delivery

- iOS 15 physical device: launch, provider request, tool round-trip, Linux command exit 0, browser operation, persistence, attachments, long-list streaming and foreground/background return.
- iOS 16+ regression for retained native paths.
- Produce an unsigned/signed artifact as explicitly authorized, with source/build provenance and remaining feature limitations.
- Do not label the work complete until the iOS 15 runtime acceptance checks pass; buildable is not the same as usable.

## Work discipline / retirement track

- Only task-related changes. No unrelated refactoring or generated dependency/vendor churn.
- Preserve native implementations; retire only unguarded legacy call sites after their replacement is verified.
- Inventory all remaining iOS 16+ symbols and link requirements; a source scanner is an aid, not proof of compile availability.
- Keep a checkpoint in `docs/progress.md` after each gate, with files changed, tests run, result, blocker and resume instruction.
- No commit, push or public release without the required user authorization.
