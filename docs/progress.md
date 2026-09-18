# Progress

## 2026-09-18 — e26f9fd rendering API follow-up

- Locked local/fork e26f9fd; run35336915696 failed18:56:04 in full app, while expanded15/16 smoke passed (including FileProvider/badge controls). Nonempty875010-byte log, exit65 and fixed-toolchain provenance: reports/openminis-ios15/run-35336915696-Gef7b2/. The previous app-startup diagnostics no longer appear.
- Five diagnostics at four call shapes: toolbar secondaryAction, Color.gradient, async AVAssetImageGenerator.image(at:), and markdown.ranges(of: Regex). A whole-tree scan found no additional direct uses of these shapes outside the four diagnosed sites.
- Plan: native secondaryAction/gradient retained on16+, older trailing toolbar/explicit gradient on15; video generation stays asynchronous using the existing callback API below16, with the same generator transform/size and caller-owned cache/onLoad; Foundation regex replaces ONLY diagnostic image-token scanning, not cmark rendering. Test full-range Unicode handling and compare actual helper output against the old Swift regex on the macOS runner.
- Read the video attachment task, cache/notification path, existing callback-based video player, shared ThumbnailCache and MarkdownStripper. Minimal seams: add a static frame-loader helper to existing ThumbnailCache and image-token helper to existing MarkdownStripper (both already in app target). Extend real compiler smoke to those production files and exact failing API controls.
- Success requires positive15/16 compiler checks, Foundation fixture equivalence and fresh full build. Independent failure signals: synchronous decode on UI, changed cache keys/size metadata/onLoad notifications, missing or duplicate continuation completion, image diagnostics corrupt CJK/emoji ranges, or a hidden copy action on15. Do not treat diagnostic error counts as a completion percentage.
- Implemented and reviewed the six-file production diff. The two supporting files already have all four Xcode project references. Local structural test failed before edits and passes after:10/10; parser8/8; toolbar fixtures7/7 and zero findings; shell/diff checks pass and nine modified/new Swift files have no added parse errors. Test fixtures use the same Swift Regex engine via its constructor (the local parser does not understand the bare literal); no compiler error was suppressed. Real Apple smoke now includes eight production/support files and a host Foundation comparison for13 fixtures; those new cloud results remain pending.

## 2026-09-18 — f27b5a7 app-startup integration boundary

- Fresh local/fork baseline is f27b5a7; run35332863701 failed at18:08:10 with four diagnostics in MinisApp (domain initializer, badge API, replicated-domain removeAll). Expanded smoke passed15/16. The pre-existing local badge branch is retained; no new push occurred through18:46.
- Correct Swift mechanism: `@available` for the lazy static domain, runtime `guard #available` for execution. `#if #available` is NOT valid Swift and will not be written. Do not replace the replicated domain with an invented legacy storage path.
- Preserve the unconditional registerFileProviderDomain call: its directory/Soul initialization must run on15. Return only AFTER that setup and BEFORE FileProvider cleanup/reset/registration. Signal and watcher start independently no-op below16. Domain reset generation, callbacks, backup/restore and modern behavior remain unchanged.
- BackgroundKeepAliveManager is explicitly @MainActor. Its legacy badge path can assign applicationIconBadgeNumber directly, respecting the existing enabled/count policy without an extra asynchronous hop. Preserve native setBadgeCount and logging on16+; foreground clears0 on both branches.
- Add structural red/green checks for initialization order, guarded domain/signaling/watcher and badge policy; extend Apple negative/positive fixtures for FileProvider and badge API shapes. Full-app compile remains required. No filesystem data or reset logic is deleted/reimplemented.
- Implemented the boundary in three production files. Before the fix all three new checks failed; afterwards structural tests9/9, parser8/8, toolbar audit tests7/7 and zero toolbar findings. Shell syntax/diff checks pass, five changed Swift files have no added parser errors. Reviewed the production diff: no changes to reset generation/snapshot/restore callbacks or core initialization order. Cloud compile pending.

## 2026-09-18 — c3eacb9 full-build follow-up: conditional toolbar builders

- Baseline local/fork `c3eacb9a9315e9e3d59a30fd447d8cba83acc567`, clean before this batch. Run `35330747608` failed at 17:43:04 in the full app, while expanded iOS15/16 smoke passed. Artifact `reports/openminis-ios15/run-35330747608-M8WlyJ/` has 800024-byte log, exit65, matching versions; actions.log lines519/556 confirm smoke PASS. The nine speech/timer diagnostics no longer appear.
- Two reported diagnostics come from ONE `if hasChanges { ToolbarItem { Save } }` in SkillFileDetailView. `ToolbarContentBuilder.buildIf`/Optional ToolbarContent needs iOS16; this is not the same builder as conditional Button content inside a ToolbarItem.
- Read the enclosing editor/save action and all toolbar-declaration owners. AST sweep (nearest closure/builder ownership, existing availability awareness) found eight legacy-visible conditional toolbar owners, including the reported editor and seven others. Extend `plan_ios15_adapters.py --check-toolbars` plus seven scanner fixtures; prove eight findings/exit1 before production edits.
- Plan: keep each ToolbarItem unconditional and move visibility/branching into its ViewBuilder content. Preserve placements, actions, disable conditions, and all destructive-action confirmation dialogs; never wrap the entire editor/form in a state-dependent parent branch. Named multi-select toolbar follows the same rule for Cancel/Add/Done. No new compatibility abstraction needed.
- Hypotheses: result-builder availability, directly supported by compiler diagnostics; stopped compiler batches explain seven unreported owners; toolchain drift contradicted by matching provenance and passed smoke. Bisection/runtime instrumentation would not add evidence here. Add exact optional/if-else toolbar cases to compiler negative controls and fixed item-content forms to positive controls.
- Success: scanner zero, scanner tests pass, expanded Apple smoke passes15/16, then full app exit0 required. Failures independent of compilation: unchanged editor exposes Save, running backup exposes Delete, disconnected remote exposes Save Here, multi-select actions lose placement/disabled state, or a conditional parent rebuild resets editor state. Those visibility paths still require device acceptance after compile.
- Applied the eight narrowly scoped builder rearrangements; reviewed the full production diff to confirm only nesting changed, not callbacks/guards/confirmations. Local evidence: toolbar AST findings 8→0, scanner fixtures 7/7, structural suite6/6, parser8/8, shell syntax/diff checks pass, ten changed Swift files have no increased parse-error count. The AST checker uses the existing local tree-sitter environment; CI remains the real Apple smoke/full-build gate, with no new pip/network dependency.

## 2026-09-18 — 5419af1 full-build follow-up: voice punctuation and timer API availability

- Locked local/fork baseline `5419af10b82cc1ae599824d02702d87cbbb35699`, clean tree. Run `35327969342` failed at 17:11:58 in the full app probe, not in the compatibility smoke.
- Fresh artifacts: `reports/openminis-ios15/run-35327969342-5pqmzP/`; 816042-byte xcodebuild log, exit 65, versions match the Xcode26.2/17C52/iSH pin. `actions.log` lines 483/520 explicitly show all six production compatibility files passing type-check at 15 and 16. NativeOnly's availability errors are the expected negative control, not a failing smoke.
- Actual nine diagnostics: one `addsPunctuation` (VoiceProvider+System), plus four Duration-based Task.sleep sites emitting two diagnostics each (VoiceProviderResolver and SpeechPlayerControl). Inventory found four more identical sleep calls in AIChatView that this stopped build did not diagnose.
- Narrow plan: guard only automatic speech punctuation at iOS16; preserve transcription and every other request option on 15. Convert all eight millisecond sleeps to the existing project-wide Task.sleep(nanoseconds:) convention, with the same intervals, Task ownership, try?/cancellation guards and delayed side effects. Do not introduce another clock abstraction or alter debounce policy.
- Hypotheses: (1) unguarded iOS16 request property and Duration clock API are the cause, directly evidenced; (2) incomplete compiler batches explain the additional four identical source calls; (3) environment drift is ruled out by matching versions and successful 15/16 smoke. Replayed the frozen compiler log; no speculative bisection or runtime instrumentation is needed.
- Before source fixes, extend structural regression checks to fail on Duration sleeps in chat/voice and to verify the eight exact intervals plus post-sleep cancellation guards. Add the speech/timer call shapes to the Apple-compiler positive/negative fixtures. Those checks do not replace a fresh full build.
- Success: old nine diagnostics absent from a new pinned build, smoke remains green, full xcodebuild zero required for M1. Independent failures: milliseconds accidentally treated as nanoseconds, lost Task cancellation, speech recognition itself disabled on iOS15, or expected negative-control errors reported as new app failures. Device acceptance remains outstanding.
- Implemented the one-property availability guard and all eight sleep conversions in four source files. Reviewed the production diff: Task ownership, interval values, cancellation checks and subsequent actions are otherwise unchanged.
- Local regression evidence: new checks failed on 5419af1 (three new methods, including interval subcases); after the source fixes all 6 structural tests and all 8 parser tests pass. Bash syntax, whitespace and six changed Swift files' syntax delta pass; unlike the earlier ad-hoc scanner this delta check exits nonzero on new parser errors. The new Speech/Duration positive/negative compiler fixtures await the next cloud run.

## 2026-09-18 17:10 — compiler batch 2: f644f41 + five diagnosed + inventory sweep

- Baseline f644f41, run 35316524791 failed (5 diagnostics). Evidence in reports/openminis-ios15/run-35316524791-tZZ8D9/; replayed by scripts/ios15_build_log.py. Plan: docs/ios15-compiler-batch.md.
- Fixed the five diagnosed calls: WebPreviewSheet WebKit fullscreen (gated 15.4) + persistentSystemOverlays (compat), ToolLiveSheet UnevenRoundedRectangle (CompatUnevenRoundedRectangle), two CollectionViewMessageListV3 contextMenu previews (compatContextMenu keeps all actions on 15, drops only preview).
- Compatibility layer repairs: removed duplicate compatLineLimit; retired compatToolbarVisibility (leaked ToolbarPlacement) and compatTextFieldAxis (silently single-line); added CompatMultilineTextField (native vertical TextField 16+, TextEditor 15+), CompatAnyShape, CompatUnevenRoundedRectangle, compatNavigationBarHidden, compatPersistentSystemOverlays, compatContextMenu, compatFontWeight.
- Swept remaining same-shape call sites: multiline TextFields in ContentView folder sheet, MCPFormSheet, ProviderInstanceDetailView, InlineVoiceInputView; UnevenRoundedRectangle selection band and folder shapes in ContentView; View.fontWeight on Images/Buttons (AIChatView/ContentView/AssistantBlockView/BackupSettingsView); Locale.language.languageCode to compatLanguageCode; ShareLink and UITextView(usingTextLayoutManager:) in LogManagementView/MinisMediaViews.
- Added scripts/test_ios15_compat_contract.py (3 structural guards, fail on baseline), extended NativeOnly negative control to the real rejected shapes, CompatibilityCalls to the production adapters; check script now type-checks all six Shared modules at 15 and 16.
- Workflow now runs contract tests + real SwiftUI smoke before the app probe; smoke/contract failure keeps the job red.
- Local checks: 3/3 contract, 8/8 parser, bash -n, tree-sitter delta clean, git diff --check clean. Awaiting cloud compile gate. M3 device acceptance still outstanding.

## 2026-09-18 13:50 — batch compatibility complete, pushing for compile gate

- 兼容层落地（6 个 Shared 文件，pbxproj 已全部注册：BuildFile/FileReference/Group/Sources 各 4 处引用齐全，含 LegacyFlowLayoutTests 的同步目录豁免确认）：
  - `SwiftUICompatibility.swift`：CompatNavigationStack / CompatLabeledContent（泛型+value 变体）/ CompatPresentationDetent / sheet·scroll·toolbar 适配器。
  - `CompatNavigationPath.swift`：CompatPathNavigationStack（[Element] 绑定）/ CompatValueNavigationLink / CompatSplitNavigationView（iOS15 = NavigationView 双栏）。
  - `CompatPhotoPicker.swift`：CompatPhotoPickerItem（iOS16 PhotosPickerItem / iOS15 PHPickerResult 双后端，视频自拷贝临时 URL）/ compatPhotosPicker。
  - `CompatGeometry.swift`：compatOnGeometryChange（iOS15 PreferenceKey 模拟）。
  - `LegacyHostingContent.swift`：iOS15 UIContentConfiguration + UIHostingController 承载；`SelfSizingCell.applyHostedContent(parent:content:)` 统一双路径。
  - `LegacyFlowLayout.swift` + `MinisTests/LegacyFlowLayoutTests.swift`（4 用例）。
- 调用点：40+ 文件批改（Backup/AIChatView/ContentView/Settings/Sync/Provider/MCP/Rootfs/语音）；ContentView 路由已改 [String] + CompatPathNavigationStack；SettingsSheet 深链已迁移 destination 闭包。
- 工具：`scripts/ios15_build_log.py`（去注释误报的诊断提取器，8/8 回归）+ `scripts/plan_ios15_adapters.py`（tree-sitter 编辑 planner，批次 JSON 在 reports/openminis-ios15/batch-adapter-plan/）。
- 工作流升级：诊断改用脚本、加 cache/save（红 probe 不再丢原生缓存）、加单测步骤。
- 本次 commit 推 compat/ios15 触发云端编译=唯一编译门禁。预期第一次仍可能红（导航递归 link 方案、承载层编译细节未经验证），按真实错误收敛；BUILD SUCCEEDED 才算过。M3 真机验收仍未进行。

## 2026-09-18 10:22 — Batch compatibility before the next push (current instruction)

- User: “嗯，基本做好兼容再去推”. Do not push a tiny page fix just to discover the next compiler error. Supersedes the previous one-layer-per-push approach.
- Keep work local until the major iOS 15 paths are implemented and inspected together: navigation (including route state), message hosting/lifecycle, media picking/layout, simple form/sheet APIs, and system-integration availability. Preserve native paths on supported versions.
- Next remote build is an integrated verification gate, not proof that unchecked local code already compiles. No local Xcode/Swift is installed. Do not promise one-pass success or an installable IPA before cloud and device validation.
- Local changes remain based on `1410ef0`, no new commit/push/run. BackupRestoreView's 13 diagnosed calls have now been switched. Log-parser regression tests passed 8/8, with four failures demonstrated against the old extractor first. Swift smoke has only passed shell syntax locally; Apple type-checks are pending.
- Independent review was not available: the exposed spawn payload includes ACP-only `streamTo`, rejected for subagents. No child started. Stop retrying this tool combination; do not change the gateway or claim independent review.

## 2026-09-18 — Resume M1: backup/restore availability layer (in progress)

### Locked baseline and red evidence

- Local and fork `compat/ios15` both point to `1410ef040c78ca03be7000f88ccfd0ad5928daa0`; preserve the pre-existing uncommitted 23:50 progress entry below.
- Latest completed CI: `35243806282`, failed at 2026-09-18 00:06 Asia/Shanghai. Downloaded the nonempty 888,385-byte compiler log and `versions.json`; provenance confirms Xcode 26.2 / 17C52, SDK 26.2, Swift 6.2.3, pinned iSH `3f6384c`.
- There are **24 real compiler diagnostics**, all in `BackupRestoreView.swift`: 11 `LabeledContent` sites each emit a type and initializer error, plus `NavigationStack` at 924 and `presentationDetents` at 1315. The old summary's 42 includes 18 source-comment echoes containing `error:`.
- The real red-capable feedback loop is the pinned macOS compile in `.github/workflows/ios15-m0-baseline.yml`. This host has no Xcode; local source checks are not a substitute. The diagnostics directly identify unavailable APIs, so speculative bisection is not useful here.

### Plan, hypotheses and ownership

1. Keep compatibility views in a small, independently type-checkable SwiftUI file. Mechanically move the existing navigation / sheet / scroll adapters out of `AppLocalization.swift` without changing their behavior; register the new file in the app target. Add `CompatLabeledContent(LocalizedStringKey, value: String)` and a height-only sheet adapter accepting `CGFloat`, never an unavailable `PresentationDetent` in a legacy-visible signature.
2. Change only the 13 diagnosed backup/restore call sites. iOS 16+ retains native controls. iOS 15 uses a localized label/value row, the existing stacked `NavigationView`, and a standard sheet. Preserve cancel, interactive-dismiss protection, import/download state and backup formats.
3. Before production edits, add a small Apple-compiler smoke fixture for the actual compatibility module. A native-API negative control must fail for iOS 15 and pass for iOS 16; adapted calls must type-check at both targets. Obtain independent plan/diff review.
4. Tighten the existing compile loop: unit-test a diagnostic parser against source-comment false positives, and save successfully built native dependencies before the intentionally red app probe rather than only on whole-job success. This is CI-only; retain pinned toolchain/submodule and failed xcodebuild exit status.

Success for this checkpoint: compatibility smoke passes at 15/16; all 24 old backup diagnostics disappear from a fresh full compile; native cache survives a later Swift failure; diagnostic summary counts actual compiler errors. A later file's availability failure is a new layer, not global M1 success.

Independent failure signals: missing Xcode target membership; localized labels become verbatim or dynamic values become localization keys; iOS 16 loses native navigation/form semantics; cancellation/dismiss guards change; native cache saves partial output; parser hides an unlocated compiler/linker failure or turns a red build green.

Ablations: raw iOS-16-only controls must fail the iOS-15 smoke and compile at 16; guarded controls must compile at both; feed the observed comment into the log parser and require zero diagnostics from that line. Full app compile remains authoritative, and runtime / iOS 15 device acceptance is still outstanding.

## 2026-09-17 23:50 — M1 layer: LocalizedStringResource / NavigationStack

- Last probe `35237699911`: 54 errors in the app (FileProvider isolated). Dominant: LocalizedStringResource, NavigationStack, buildIf, Regex, presentationDetents, scrollContentBackground, View.bold/fontWeight.
- Fixes in this checkpoint: `AppLocalized` takes `String` (iOS 15 bundle lookup); `LocalizedStringResource` overload gated to 16+; idle option labels are `String`; `CompatNavigationStack` for the four failing stacks; env-var key check without Regex; sheet detents / hidden scroll background / toolbar `if` availability wrappers.
- Expect the next CI run to fail on the *next* availability layer, not these 54.

## 2026-09-17 22:42 — M1 method after first probe

- Run `35232182546` failed as intended. 17 errors, all FileProvider `NSFileProviderRequest` / ItemVersion / ItemFields (iOS 16). Main app never compiled.
- Method: peel compiler layers. Do not rewrite File Provider for iOS 15. Keep that extension at 16.0 so it stays out of the iOS 15 product. Lower only Minis + Share to 15.0. Recompile without a global deployment override so the next errors are NavigationStack / UIHostingConfiguration / PhotosPicker.
- For those: add a narrow compatibility path, keep the iOS 16+ implementation behind availability. No global type-name shadowing. Repeat until `Minis.app` compiles at 15, then device runtime.
- iOS 15 will not get Files-app mount, Live Activities, AlarmKit, App Intents, or CKSyncEngine sync. Local SQLite, chat, sandbox stay in scope.

## 2026-09-17 22:11 — starting M1 compiler probe

- M0 remains the last green compile: run `35228590640`, unsigned Debug `Minis.app`, not iOS 15.
- No Swift compatibility patches yet. Local inventory: 442 Swift files, NavigationStack 51/92, UIHostingConfiguration 6/35.
- Next CI on `compat/ios15` caches native deps and runs `xcodebuild IPHONEOS_DEPLOYMENT_TARGET=15.0` to list real availability errors. Expected red. Do not treat that red as a toolchain regression.

## 2026-09-17 21:54 — M0 baseline compile passed

- Run `35228590640` **success** in 13m34s. Commit `838ddebf617b834d8b08b0b3ea74e04d8cdf6717`.
- Evidence: Xcode 26.2 (`17C52`), iOS SDK 26.2, Swift 6.2.3, iSH `3f6384c70eefd1a370f121d3492a5f21f7767df9`, xcodebuild log 2,241,622 bytes, `** BUILD SUCCEEDED **`, `Minis.app` exists, Info.plist SHA256 `870883f6e6b0d44bc98b4e24e1abaee9c0775a13a4cf51ed9ca631b0a0500876`.
- This is an unsigned Debug generic-iOS compile of unchanged upstream plus CI/docs. Not an iOS 15 runtime proof.
- A 21:45 isolated check job likely timed out and is not the compile result. Next: M1 deployment-target / availability, without another full native rebuild unless the workflow changes.

## 2026-09-17 21:39 — M0 native fail: iSH VDSO missing lld

- Run `35225046741` failed in 5 minutes. Xcode 26.2 pin and iSH SHA passed. LAME and FFmpeg 6.1.2 packaged successfully.
- Fail: `deps/build_ish.sh` ninja `[1/90] vdso/arm64/libvdso.so.elf` → Homebrew clang 23.1.0 `invalid linker name in argument '-fuse-ld=lld'`.
- Not an iOS 15 Swift issue. App compile never started. Evidence step then failed because `$RUNNER_TEMP/m0` did not exist.
- Fix: install Homebrew `lld`, put `ld.lld` on PATH, mkdir evidence dir. Re-run on `compat/ios15`.

## 2026-09-17 evening — M0 fork + baseline CI

### Baseline

- Upstream remains `OpenMinis/OpenMinis` `4ef29002e88db1e20e462ec2ff46916e8a7dcb45`.
- Disk ordinary availability recovered to about 1.2 GiB after cancelling the unrelated OpenClaw upgrade cleanup. Still no local Xcode.
- User asked to continue OpenMinis, then said start, then asked why no new GitHub repository existed. 21:04: “我全部授权你，后续的话不用我同意.” Subsequent fork/commit/push to `doimty/OpenMinis` non-default branches, Actions, and compatibility code do not need another ask. Still do not push upstream `OpenMinis/OpenMinis` or `main`.

### This checkpoint

- Added `.github/workflows/ios15-m0-baseline.yml`: pin macOS 26 / Xcode 26.2 / iOS SDK 26.2, init iSH `3f6384c70eefd1a370f121d3492a5f21f7767df9`, build lame/ffmpeg/ish/rootfs/rclone, compile `Minis` for `generic/platform=iOS` with signing disabled.
- No Swift compatibility edits and no deployment-target change in this checkpoint.
- Resume: after the fork exists and the workflow run finishes, treat only a zero `xcodebuild` with nonempty log + `versions.json` as M0 pass. Then M1.

## 2026-09-17 — iOS 15 port started, M0 authorization gate

### Baseline

- Upstream: `OpenMinis/OpenMinis`, `main` at `4ef29002e88db1e20e462ec2ff46916e8a7dcb45`.
- Work branch created: `compat/ios15`.
- Initial working tree was clean. No user modifications were overwritten.
- Product scope: iOS 15 core edition; preserve iOS 16+ native paths where practical.
- Authoritative implementation plan: `docs/ios15-port-plan.md`.

### Completed

- Rechecked source SHA, target settings, workspace cleanliness and native-tool availability.
- Created a task-specific branch, without a commit or push.
- Recorded hypotheses, failure signals, ownership boundaries, native/legacy ablations and runtime acceptance gates in the plan.
- Ran `bash -n` on `deps/build_ish.sh`, `deps/build_ffmpeg.sh`, `deps/build_lame.sh` and `deps/prepare_alpine_rootfs.sh`: all passed shell syntax checks. None of these scripts was executed.

### Current blockers / limitations

- No local `xcodebuild` or Swift compiler. Xcode compilation has not been attempted and no IPA exists.
- Filesystem ordinary available space is 0 bytes; remaining free reserved blocks are approximately 1.3 GiB. Do not install/download a local toolchain or large native artifacts.
- Need explicit approval for GitHub fork/repository creation, commit/push of a non-default work branch and macOS Actions execution. No GitHub write has occurred.
- Independent child plan review did not run: runtime rejected the incompatible `streamTo` argument on a subagent spawn. Do not count failed launch attempts as a review or claim background implementation is running.

### Resume

1. Obtain user authorization for the GitHub write/build operations above.
2. Check authenticated repository ownership and current macOS runner/Xcode availability; pin actual versions.
3. Produce an unchanged-source baseline build and dependency lock before interpreting iOS 15 compiler errors.
4. Follow milestones M1–M3 in `docs/ios15-port-plan.md`; update this file after each verification gate.

### Not done

No Swift compatibility code has been changed yet. No targets lowered, no dependency scripts run, no fork created, no commits or pushes, no build or device validation. This checkpoint records the start and a genuine build-environment/authorization gate, not completion of the port.
