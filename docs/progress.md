# Progress

## 2026-09-19 — device rejects the latest layout fix; screenshot attribution corrected

- User explicitly confirms the previously supplied chat screenshot is from the **latest `161a1c8` IPA**, not an earlier build. Treat it as failed device acceptance. Do not ask for another identical screenshot or a reinstall to establish its version.
- Fresh baseline check: local HEAD and `git ls-remote fork refs/heads/compat/ios15` both resolve to `161a1c84fc861e43107c30896d16ac2a68992d09`; preserved IPA SHA256 is `e265e4df530163ad45df558355b04d51bd4f04d128a0cf33d5c50be0bcb49fa2`. No production code changed during this follow-up.
- `python3 scripts/test_ios15_compat_contract.py -v` still passes 14/14. These are structural checks, **not a rendering regression test**; they cannot overrule the device failure. Local `bash scripts/check_ios15_swiftui_compat.sh` stops at line 5 (`xcrun: command not found`). The current cloud workflow performs an unsigned generic-iOS build, not an iOS 15 render/test run.
- Earlier claims that screenshot whitespace proves a cell-height bug, a bottom-inset bug, or a need to bottom-align short conversations were not measured. Likewise, the exact missing text and horizontal root cause are unverified. Do not change scroll alignment or ship another speculative frame patch merely to fill the screenshot's whitespace.
- Feedback-loop gate remains open: existing `MinisUITests/MessageListV3UITests.swift` launches `--uitest-message-list`, but was not run on an Apple runtime here. `MessageListSnapshotCollector` records actual geometry only when enabled; it is DEBUG-only and off by default. No captured geometry trace exists for this report yet.
- Lowest-friction next artifact: Settings → Logs → the relevant daily `minis-YYYY-MM-DD.log` → share from the detail page (iOS 15 sharing is implemented in `LogManagementView.swift`). Request the file, not manual keyword filtering, and inspect privately. If it lacks geometry, use the existing debug snapshot seam to create a bounded diagnostic capture rather than inventing another cause.


## 2026-09-19 — iOS 15 usability: session-row tap + legacy chat height fix

- User feedback after the SwiftUI-crash fix: chatting works, but layout is off, and the session list only opens once at launch — tapping rows never navigates.
- Row tap: `CompatValueNavigationLink` renders as a zero-size `Button` (EmptyView label) on iOS 15, so the hidden background link was never tappable. `stackList` now applies `compatLegacyNavigationTap { navigationPath.append(session.id) }` (iOS 15 only; iOS 16 keeps native value-link activation).
- Layout: (1) the legacy PLAF branch accepted only width-matched reports; a strict `< 2pt` width gate rejected valid GeometryReader reports (margins/safe-area), freezing cells at coarse estimates — gate now accepts any sane finite height and mirrors the native <4pt no-cache policy. (2) `LegacyHostingContentView` no longer pins `host.view`'s bottom edge, so Auto Layout sizes the hosting view to its intrinsic SwiftUI height and the GeometryReader reports the true content height instead of the cell's current estimate.
- Structural suite 14/14 local; packager 10/10; parser 8/8. Needs a fresh cloud build + device retest.

## 2026-09-19 — iOS 15.1.1 SwiftUI legacy-hosting crash diagnosed and patched

- New raw system `.ips` from iPhone14,3 / iOS15.1.1, incident `29F10B9B-BD7D-4509-8019-4C4BF6937016`, is a different/new-build crash. `Minis.debug.dylib` UUID `ff55af62-e986-3005-a22f-c2a83d8ee1f7` confirms it is not the old IPA.
- Stack: `swift_beginAccess` → `LayoutComputer.EngineDelegate.explicitAlignment` → `AttributeGraph` → `LegacyHostingContentView.measuredSize` → `systemLayoutSizeFitting` → `SelfSizingCell.preferredLayoutAttributesFitting` during diffable update. This is iOS15 SwiftUI graph re-entry, not the repaired `CrashReporter.appendLog` lock.
- Patch: iOS15 `SelfSizingCell` now bypasses both `super.preferredLayoutAttributesFitting` and synchronous `UIHostingController.systemLayoutSizeFitting` for `LegacyHostingConfiguration`. It uses the layout estimate until `LegacyHostedRoot`'s `GeometryReader` asynchronously reports a size, then returns that measured height and invalidates the layout. iOS16+ path unchanged.
- Local structural suite 13/13, IPA packager 10/10, parser 8/8, diff check pass. CI run `35419963633` passed the smoke, full app compile and IPA packaging. New IPA SHA256 `361023bade2c21ef0c86f8e7a3ef7dc7e18970af639fcbf4379922d6b5e98f18`; release `ios15-trollstore-1.13-a83895b`. Device verification remains outstanding. Local host cannot run Apple smoke (`xcrun` absent); toolbar audit dependency (`tree_sitter`) is absent.

## 2026-09-19 — iOS 15.1.1 PAC crash in CrashReporter.appendLog

- User installed the TrollStore IPA on iPhone14,3 / iOS 15.1.1 (19B81). App launched then died: EXC_BAD_ACCESS SIGSEGV, `possible pointer authentication failure`, queue `com.apple.uikit.datasource.diffing`.
- Stack: `SelfSizingCell.preferredLayoutAttributesFitting` → `AppLogger.info` → `CrashReporter.appendLog` → `swift_beginAccess`. Evidence `reports/openminis-ios15/crash-2026-09-19/` (workspace, not this repo). Binary is arm64 not arm64e; dyld loaded Minis.debug.dylib; this is not the chained-fixups launch abort.
- Cause: `os_unfair_lock` stored as a Swift `var` on the same object that mutates other properties. `&logRingLock` starts exclusive access on self; mutating `logRing` then traps on iOS 15's exclusivity runtime. Same pattern in MessageListLayout streaming lock and BrowserTabPool.GateBox.
- Fix: those locks are `NSLock` `let`s. Structural test `test_os_unfair_lock_is_not_a_swift_stored_var` failed on the four stored vars first (12/12 after). No new files, no pbxproj. Remaining after the next IPA: M3 device acceptance on this same phone.

## 2026-09-19 — TrollStore IPA delivered (e8b5c22 / 35412629983)

- User is on TrollStore. Unsigned/ad-hoc Debug IPA, not Developer-ID.
- Baseline `e8b5c22d0630bb6edf1ba0b0694199d6cec13638`. Run 35412629983 success in 8m36s. IPA `Minis-1.13-ios15-trollstore.ipa` 84163359 bytes, SHA256 `fe64fde471ed608f7830e33b2f242fc11d732d127d791146980245b42dd4321b`, MinimumOS 15.0, ad-hoc signed, Share kept, FileProvider 16.0 and Widget 16.2 stripped.
- Prerelease: https://github.com/doimty/OpenMinis/releases/download/ios15-trollstore-1.13-e8b5c22/Minis-1.13-ios15-trollstore.ipa
- First packager CI 35412567522 failed only the `/var` vs `/private/var` path equality test; production packager was unused that run.
- Remaining: M3 device acceptance after install (launch, provider, Linux command, persistence, attachments, streaming, VAD, 16+ regression).

## 2026-09-19 — 1cbcdfa: VAD 15.0 local package linked, app compile gate CLOSED

- Baseline `1cbcdfa27d6ef58dc8f000ff52ed7b50dff59c6f`. Run35382826109 succeeded; app xcodebuild exit0, zero errors, `build_succeeded=true`, log 2,274,514 bytes. The app now links a **local vendored Swift package** (`vendor/RealTimeCutVADLibrary`) whose binaryTarget is an xcframework rebuilt from pinned C++ source at `IPHONEOS_DEPLOYMENT_TARGET=15.0` by `deps/build_vad_framework.sh` (native-deps step, cached).
- **The 15.6 linker warning is gone** (previously app log line 17992). No native dependency in the app link floor is newer than 15.0. This closes the last compile-level deployment blocker; iOS 15.0-15.5 dyld will accept the app's binaries.
- Integration iteration (each red fixed with a red-first regression test, local suite 42/42): chmod 100755 (4533369) → create-xcframework silent failure, tried plain-framework copy (52a8aa7, WRONG: Xcode 26 rejects non-xcframework binaryTarget) → `grep -Fq` for the success marker (e744e66; BSD grep treats `**` as invalid BRE) → hand-assembled xcframework but framework-style wrapper plist (9fcd3c4) → plistlib AvailableLibraries shape (1d57574) → **real create-xcframework shape** (1cbcdfa): `CFBundlePackageType=XFWK` (not XFWKIT) + required `XCFrameworkFormatVersion=1.0` + FMT_XML + `plutil -lint` gate in-script. Verified against the pinned upstream onnxruntime.xcframework's actual Info.plist.
- The vendored wrapper source+models are byte-identical to upstream b0596088 (MIT LICENSE present); Package.swift differs only in the local binaryTarget path. The rebuilt CXX binary is built in CI and never committed.
- **Remaining:** (1) build an installable IPA (unsigned for now; signing team `29S5S789Z7` needs a real cert/profile for device install), (2) M3 iOS 15 device acceptance (launch, provider request, Linux command exit, persistence, attachments, long streaming, voice VAD/model execution, foreground/background, drag/drop arbitration, 16+ regression).

## 2026-09-18/19 — 4cc72bc: VAD native floor source-build probe APPROVED

- Baseline `4cc72bc2fa861600264abe1af784d962664e6259`. Run35375830915 succeeded17:42:32Z, nine gates all ok, exit0. The probe rebuilds upstream C++ source at `IPHONEOS_DEPLOYMENT_TARGET=15.0` (device arm64, signing off) against checksum-pinned ONNX/APM inputs, audits input and output Mach-O minimum/platform/arch/Info.plist, checks all 7 C entrypoints as real `nm -gU` `T` exports, validates the dynamic dependency closure, and compiles the real upstream ObjC wrapper against the rebuilt framework. Evidence `reports/openminis-ios15/run-35375830915-af6ERo/`, JSON SHA256 `42633aadf79cf9592e81f61079fe6ac0e7d3c473e32ab27460f024b1bfd82759`; binary SHA256 `2fa4125cf5e232609d1e00b1301258724bd0d6ced32c7ae3379375dbeb18d596`; wrapper object SHA256 `51d20515ba872705c22cb961a36f78ba6acd50610963c9b2d33a800ce177fd8b`; build log 191,960 bytes with one BUILD SUCCEEDED. Confirms the source rebuild route is real, not a metadata patch.
- Probe iteration chain (each fix backed by new red tests, current fixture suite 36/36): toolchain grep double-prefix (565932b) → input extraction dir + clang path (9dcf120) → macOS case-insensitive `-l`/`-L` evidence filename collision + `nm -gU` exports + full-gate/exit0 approval + no evidence truncation (034fbbf) → `otool -L` image-header line misread as dependency (4cc72bc). Earlier probe runs: 35369830724 (toolchain gate), 35371955041 (inputs audit numeric platform), 35372425912 (build rc65 missing XCFrameworks), 35373342504 (build ok but output audit read overwritten -L file), 35374575871 (dependency header false positive).
- The probe is evidence-only: it does not change app linkage, publishes no framework/IPA. **Remaining: (1) integrate a pinned 15.0-built framework into the app** (fork/vendor the rebuilt artifact + SPM binaryTarget swap or local .xcframework path; resolve the missing-LICENSE notice/provenance record before any public redistribution), (2) re-run the full app build and confirm the 17992-line 15.6 linker warning is gone, (3) build an installable IPA, (4) M3 iOS 15 device acceptance (launch, provider request, Linux command exit, persistence, attachments, long streaming, voice VAD/model execution, foreground/background, drag/drop arbitration, 16+ regression).

## 2026-09-18 — aa21abc compile success, native-floor gate still open

- Current local/fork baseline `aa21abcbecf2030004e6675dbb8ee6cd2f2e6721`. Run35357606053 succeeded14:50:29Z: native dependencies actually rebuilt, compatibility smoke passed at15/16, Foundation13-fixture and NSItemProvider tests passed, full Minis app build exited0 with zero compiler errors. Evidence `reports/openminis-ios15/run-35357606053-q64sS2/`, nonempty2,275,679-byte app log, SHA256 `df8ddb6377eb3ffca26dfe6139661ea7a61158083b9845da05a2f23192df7f61`. This is compile evidence, not install/runtime acceptance.
- First full compile success was535b84f/run35354849516. That link exposed11 rclone object minima16.0 plus one VAD dylib minimum15.6. The rclone script's actual device/simulator flags were lowered to15.0 and rebuilt inaa21abc; its newer-OS warnings are now0. The VAD warning remains at current app log17992. Do not suppress it or silently raise the15.0 product floor.
- Late source chain, correcting earlier incomplete status:26d20fb/run35351187250 failed the native fixture arity;177ce91/run35351855452 then exposed the production drop action signature;4a4d5c4/run35352396007 passed smoke and exposed row separator guides;d990ce8/run35353243695 exposed two ProposedViewSize signatures;535b84f/run35354849516 passed full compile. The last fix only availability-gates the two representable methods; it does not invent a representable intrinsic-size property or fixed-size substitute.
- The smoke actually contains9 production/support files after LegacyStringDrop was added. Its old `all eight` label is inaccurate; reporting is being changed to the array count without altering test execution.
- Verified VAD investigation: wrapper1.0.14 is Objective-C/resource source plus a prebuilt native binary. Native C++ source candidate1648fc1 explicitly targets15.6 at target level,18.2 at project level. The current detector initializes VAD BEFORE installing the microphone tap, so an availability guard alone is neither binary isolation nor a proved capture fallback. Keep current voice behavior while validating a source-built replacement.
- Next bounded validation: `docs/ios15-native-floor.md`. Add a standalone, evidence-only native probe on the existing pinned Mac toolchain; verify downloaded native inputs' hashes/platform/minima, rebuild source for15.0, check output and real wrapper C ABI. Do not change app package linkage until that probe passes; no new third-party distribution/publication yet. Device install, voice/model execution, M2 behavior checks and M3 acceptance remain outstanding.

## 2026-09-18 — 66b9d7c sidebar drag/drop compatibility

- Baseline local/fork66b9d7c, clean. Run35339178599 failed19:24:51 in ContentView with five diagnostics: two draggable, two String dropDestination, one split-column width. Artifact reports/openminis-ios15/run-35339178599-ls77Iz/ has814621-byte log/exit65 and matching versions. The eight-file15/16 smoke and13-fixture real Foundation regex equivalence test both passed; those are actual run results, not local guesses.
- All five direct uses are in ContentView. Existing attachment reorder already uses onDrag/onDrop; do not invent a gesture recognizer. Retain native16+ draggable/dropDestination and custom split width; 15 uses system onDrag/onDrop with NSString payloads and default NavigationView column sizing.
- Preserve id-only drag payloads, both existing ChatStore.setFolder handlers (move into folder and back out to date bucket), targeting highlights, menu fallback and modifier positions. Legacy String loading is asynchronous, ordered, best-effort for readable providers, and dispatches one nonempty result batch to the existing main-actor action. On15 acceptance means a readable provider was accepted before decoding; it cannot synchronously reflect the later action Bool. Failed/unsupported providers must not trigger an empty move.
- Add a small Foundation-only LegacyStringDrop loader and register it explicitly in the app target; real NSItemProvider round-trip tests run on the Mac CI host with a30s process bound. Positive/negative Apple controls cover all five call shapes. Structural checks fail first on the existing direct uses. Full app build and device gesture arbitration still required.
- Independent failures: applying one move per provider, reordering IDs, mutating folders on decode failure, updating State off-main, replacing context menus with custom gestures, or forcing legacy split width beyond system layout constraints. No persistence schema/action semantics changed.
- Implemented and reviewed the six-file production diff. New `LegacyStringDrop` (Foundation-only, registered in the app target with four pbx references) and `LegacyStringDropTarget` (Binding-owning onDrop wrapper). ContentView keeps both setFolder actions, id-only payloads, targeting highlights and the menu fallback; the miswritten `.listRowInsets(Edges())` was restored to EdgeInsets. Structural tests went red on the baseline direct calls and now pass 11/11; parser8/8; toolbar fixtures7/7; syntax delta zero; local checks all green. CI smoke now also runs a real NSItemProvider String-drop runtime test on the Mac host and the negative control covers draggable/dropDestination/split-width. Fresh full build remains the gate; this worktree is ready to push.
- 21:38 run35351187250 failed only in the smoke step, not production: the iOS16 dropDestination fixture overload takes ([String], CGPoint) -> Bool and the fixture's single-argument action closure was rejected. Artifact reports/openminis-ios15/run-35351187250-FBQ6aJ/ (56275-byte actions.log; versions match 26d20fb; Xcode26.2/17C52, SDK26.2, iSH match, native cache hit; m1 log empty because smoke failed before probe). Fix: `action: { values, _ in !values.isEmpty }` in NativeOnly.swift:96. No production change needed; full app build was not reached.

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

---

## 2026-09-19 14:21–14:45 — measure-width guard delivered; runtime acceptance pending

- Evidence: the private device log (SHA256 `30284b45e25b923c0314a2041f2bd1fb2e9da2fb6101d1f4721ecc38e8055530`) records a 428pt viewport, three live measurements at 1012 / 10000000 / 10000000pt, 146 bogus-layer rejection messages and a dedup height of 1020pt among other values. Numeric-only report: workspace `reports/openminis-ios15/chat-geometry-b657f1a1/geometry.json`. These prove bad geometry occurred, not the unique cause of every symptom in the screenshot.
- Candidate source path: `invalidateCellSizeIfNeeded` falls back to `textContainer.size.width` when `bounds.width <= 1`. The log does not include entry bounds proving that this fallback, rather than an already-oversized bounds value, produced each measurement. The upstream sizing failure remains unverified.
- Actual patch in `586e3f8b86cff8290bacf72e4a8066b724c3f349`: **return before measurement/cache writes** when `rawMeasureWidth` is outside `(1, max(collectionWidth, screenWidth)]`. Valid widths are unchanged. This does not clamp the actual frame, bounds, text container or SwiftUI proposal, does not add a guaranteed retry, and is not gated to iOS 15. Recovery via subsequent GeometryReader/layout passes still needs device evidence. Earlier wording calling it a proved root-cause fix or a real-width clamp was incorrect.
- Test scope: local structural suite 16/16; cloud compatibility smoke, app compile and IPA packaging passed. The numeric replay detector recognizes old failed logs and red/green log fixtures. Those fixtures test the detector, not the production rendering path. An old capture remaining red is not evidence that the source patch repairs rendering.
- Delivery provenance: push run `35427078698` is `completed/success` with head SHA `586e3f8b86cff8290bacf72e4a8066b724c3f349`. Duplicate dispatch run `35427097096` is `completed/cancelled`. The earlier upstream push was rejected; the successful push target is `fork` / `doimty/OpenMinis`, branch `compat/ios15`. Use this explicit repo for all `gh` commands; do not push `origin` or start a duplicate dispatch after a push-triggered run.
- Artifact downloaded and manifest/hash rechecked: workspace `reports/openminis-ios15/run-35427078698-586e3f8/Minis-1.13-ios15-trollstore.ipa`, 84,169,570 bytes, SHA256 `e8edc211c031eb81aa9197374b13a2aa5f575249c43b098169a5eafcd85d54e7`, MinimumOS 15.0, Share retained, FileProvider/Widget removed. Weixin delivery succeeded. No device acceptance or post-patch geometry capture exists yet.
- Resume: wait for this package's device result. If still failing, capture proposed/bounds/frame/container/collection widths in the same layout cycle and follow the upstream legacy-hosting/representable sizing boundary. Do not ship another downstream guard on the assumption that compile success implies visual correctness. This checkpoint is local documentation; no new source commit/push/build was performed while processing delayed completion events.

---

## 2026-09-19 15:10 — probe matrix found the real root cause; intrinsic-width fix shipped

- Post-delivery device log (SHA256 `b4fda347c4f7608739cf7eb0c99e0bf2060bb413c8b731e3c894f9b5d79c35c0`) still fails: viewport 428pt, 144 bogus layers, WIDTH-GUARD fired 17 times with live bounds 450 / 502 / 10000000. This falsifies the earlier claim that only a zero-bounds fallback was at fault.
- Built a minimal native API-path probe (`scripts/ios15-layout-probe/ProbeApp.swift`, `scripts/run_ios15_layout_probe.sh`, `.github/workflows/ios15-layout-probe.yml`) that compiles the **production** `LegacyHostingContent`, `ThematicBreakAttachment` and `NSTextContainerSetSizeGuard` on a pinned Xcode 26.2 / iOS 26.2 simulator. Evidence: `ios15-fallback-layout-probe` artifacts from runs `35428139345` and `35428500529`.
- Run `35428139345` reproduced the device failure exactly (396 → 653 → 1e7). Ablations proved width is driven by the **intrinsic content contract**, not by frames, margins, or the legacy host: lowering horizontal compression resistance or dropping the width demand keeps live width bounded; explicit frame and direct host do not.
- Run `35428500529` added `intrinsicAtBoundsWidth`: width bounded in **all 7 phases** (396/288), height essentially correct (152 vs 158, 196 vs 203, 327 vs 333). The residual narrow/restore height delta is the probe's static capture lagging a width change, not a layout failure.
- Production fix `a862439fb812b111e2858c5a5d0bf0e99d1d8ac1` on branch `fix/ios15-markdown-intrinsic-width`: `SelectableMarkdownView.swift` overrides `intrinsicContentSize` to report `noIntrinsicMetric` for width and measure height at the current live `bounds.width`. Plain UIKit, so it works on the iOS 15 runtime; the iOS16-only representable `sizeThatFits` override is left unchanged. Local structural suite 17/17.
- CI `ios15-m0-baseline` run `35428883271` is queued on the fix branch to compile and package the IPA. Do not treat a started run as success; verify Xcode/SDK from the log and re-download the IPA from the artifact.
- Still not device-verified. The probe is a minimal API-path matrix on iOS 26.2, not an iOS 15 runtime. Ship the IPA only after CI passes, then require the same screenshot + log loop as before.

---

## 2026-09-19 — SF Symbols follow-up

- Previous IPA provenance: final source is `f56d0ff4641f4c5e3efa844d936f4d679d1f6afe`, run `35429647024` completed successfully. The two earlier builds failed on the erroneous `QSize` return type; fixing only the working tree/test did not fix the committed production source. Final IPA: 84,166,188 bytes, SHA256 `666ecbe6c56c1fed65eb42f32ec645e84607a07d441f3d78d5c19ff0054619c2`, delivered by Weixin. That package is an **iphoneos device build**, not a simulator IPA. No new device geometry acceptance is inferred from the Add Provider screenshot.
- User clarified that many SF Symbols do not exist on iOS 15. Confirmed example: four voice templates use `mic.and.signal.meter` (introduced in iOS 16). Plan/limitations: `docs/ios15-sf-symbols.md`.
- Baseline compared with fork; branch `fix/ios15-sf-symbols` created from `f56d0ff`. Coverage gate went red on 78 late/dynamic render calls in 43 files (this counts boundaries, not 78 proven blank icons).
- Implementation: `CompatSystemSymbol` resolves requested names using actual UIKit availability, preserves supported names, uses 20 semantic fallbacks and a visible generic fallback for unknown names. Only rendering arguments changed; data, labels, actions and markdown geometry are unchanged. Native Image/Label/UIImage initializers/configurations remain in use.
- Local verification: parser/coverage regression tests green (the pre-fix test failed only at real-source coverage), prior compatibility contracts 17/17, packager 10/10, shell syntax, YAML parse, project-registration references, all fallback floors and exact-edit comparison passed. Every one of the 43 existing Swift-file diffs equals the reviewed plan. No Swift compiler is available locally.
- Native gate is now early in the pinned Xcode 26.2 workflow: iOS 15/16 API type-check plus execution of the production resolver against six explicit availability catalogs, then the existing full app build/IPA pipeline. The catalog test is not an iOS 15 runtime test.
- Independent review did not run: subagent launches were rejected due to an ACP-only streamTo parameter. Main agent owns the review/evidence; do not report a nonexistent child review.
- Final source/artifact: commit `31981ccfea891b891c3a7d99cdd16482942d2586`, push run `35431955824` **completed/success** (12m38s). Xcode 26.2/17C52, iphoneos SDK 26.2; nonempty app log 2,274,943 bytes, zero compiler error lines. Native symbol Image/Label/UIImage type-check passed for 15.0/16.0; the actual resolver passed 78,582 checks across six explicit test catalogs. The Python symbol audit passed 10/10.
- Downloaded IPA independently verified against manifest: 84,178,534 bytes, SHA256 `3a0d09f8cad3e95773c81d9738ed9c1c935943bd249a87b8aaba26e61c4c3227`; zip CRC valid, com.openminis.app minimum 15.0, Share retained, Widget/FileProvider removed. The actual arm64 `Minis.debug.dylib` contains `CompatSystemSymbol`, has platform iOS / minimum 15.0.0 / SDK 26.2.0 / UUID `7974ab20-168f-3ce3-a0c5-031275e74ddd`. The test catalog is not bundled. No incompatible-arm64e or newer-iOS link warning was found.
- Local evidence bundle: workspace `reports/openminis-ios15/run-35431955824-31981cc-x8aTSB/verification.json`, manifest, native/audit logs and app build log. Weixin file delivery succeeded, filename `Minis-1.13-ios15-SF-icons-31981cc.ipa`, message id `openclaw-weixin:1789807405533-561e687f`.
- Checkpoint: delivered, awaiting iOS 15 visual acceptance of the previously blank voice-provider/auth/sync icons. Do not call catalog tests device rendering proof, resend the same IPA, or restart an already-completed build. This final evidence update is documentation-only.

---

## 2026-09-19 — input-prompt compatibility and thinking evidence gap

- User reported that New Group cannot accept a name; screenshot SHA256 `3c93fa7034449ab77cf96c70a4d719e44e4c5e54cbd867a7776be9bcabd200ff` shows the alert without its input field. Independent read-only audit identified 7 input-alert builders / 8 TextFields, of which six flows are reachable on iOS 15; Device Name is behind an iOS 17 route. No SecureField alert exists.
- Branch `fix/ios15-input-prompts` based on clean `060a12a8ed12259a0efc8d82ebb1bcaaf86c4492`. Plan: `docs/ios15-input-prompts.md`. `compatTextInputAlert` retains native alerts on 16+, uses a normal Form sheet on 15, and reuses localized Text/field/message builders. `InputPromptLifecycle` defers callbacks until the sheet is dismissed and holds the owner's optional subject until after confirm/cancel handling. No universal blank-name rule or parent-flow dismissal was added.
- Red gate before the patch: 7 raw text-input alerts. Local green: input contracts 6/6, symbol contracts 10/10, previous compatibility 17/17, shell/Python/YAML parse, unique target registration references, new Swift syntax trees. Apple compilation and the forced-legacy UI component matrix are still pending. The matrix exercises editable CJK fields, one-shot confirm/cancel, optional rename subjects, collision follow-up, reopen, nested parent retention and modern native fields on pinned iOS26.2; it must not be presented as an iOS15 runtime test.
- Thinking report: user selected XHigh for gpt-5.6-luna. The 4,948-byte log is an exact prefix of the 207,984-byte log, not another independent request trace. Main Responses body (47,660 characters) is truncated to 3,000 in ordinary logs; title generation separately uses thinking off. No evidence yet proves a missing/incorrect main-request effort. Requested complete wire capture via chat ⋯ → Copy Requests, which reads the already-captured serialized request.
- Thinking code changes are observability only: requested level in the Responses builder, and an allowlisted final-body effort summary before the clearly-labelled body preview. No reasoning value/policy/serialization changed. Do not claim XHigh was repaired based on these diagnostic additions.
- First cloud candidate `6f0841f32ceb1e9d9d91911f2635575d0326ca3a`, run `35435115684`, passed type checks at 15/16, 38 lifecycle checks and bounded request-metadata tests. Native component rendered/edited CJK and handled first confirm/cancel, then failed to reopen (`timeout: external-cancel field`); screenshot shows the underlying view only. The early gate stopped before full App build; no candidate IPA was shipped.
- Corrective revision removes owner-flag mirroring through onChange (which can lose a coalesced false/true reopening edge). Visibility is derived from the actual owner, pending completion suppresses mid-dismissal reopen, and native appearance tracks activity. Dismissal also rechecks the actual owner to veto a save revoked before observer delivery, addressing the independent review's P2 race.
- Independent review's other P2 test gap is addressed: collision Change Name now uses the same immediate state action while the alert is present, with no inserted wait, and the modern probe has two fields/modifiers. Failure reports include current owner/active/pending/control counts. Native verification of this revision remains mandatory.
- Final verification: corrected source `889af8d99006a6a65a6da3a2424ac8d25f14e3b1`, run `35435999025` completed/success in 24m8s. All 8 native component phases, 61 lifecycle checks, 15/16 type checks, bounded request-metadata tests and full App build/package passed. Independent two-file delta review found no new P1/P2. The UI probe remains a forced legacy path on iOS26.2, not iOS15 hardware.
- Delivered IPA: 84,226,993 bytes, SHA256 `936bf1d041327dfd7395b0c4299a7967c3de10c668f5799cd9cebdf8833aa40d`. ZIP CRC and manifest/hash matched; minimum15.0, Share retained, Widget/FileProvider removed. Product binary contains the input/lifecycle/diagnostic helpers and prior symbol helper; Mach-O min15.0.0, SDK26.2.0, UUID `eab18d5d-19df-38f6-987b-9cffb7b29b6b`. Nonempty app log 2,276,886 bytes, zero error lines; no incompatible-arm64e/newer-iOS link warning found.
- Weixin delivery succeeded as `Minis-1.13-ios15-input-fix-889af8d.ipa`, message id `openclaw-weixin:1789813650018-1796e333`. Evidence: workspace `reports/openminis-ios15/run-35435999025-889af8d-eIwy59/verification.json` and sibling native/audit/build logs. Do not resend or rerun completed jobs.
- Open items: device acceptance, actual XHigh wire capture, and the user's separate chat-screen click report. A later screenshot shows the floating terminal thumbnail/tool bar and retry/error area, without identifying which control was tapped. Those click paths were inspected read-only; no click-area patch or guessed geometry fix has been applied.

---

## 2026-09-19 — retry hit-test and composer overlap fix delivered

- User confirmed the red in-chat Retry is untappable and message text paints below the composer. Two root causes, both pinned to measured device-log values:
  1. **Retry death zone**: `LegacyHostingContent` pins `host.view` top/leading/trailing with no bottom edge, so a short cell estimate leaves the capsule tail below the content view's bounds. UIKit hitTest only descends after the receiver's `pointInside` passes, so that tail is touch-dead. Fix: `override func hitTest` forwards touches inside `host.view`'s frame even below our own bounds.
  2. **Composer overlap**: bottom inset = `inputBarHeight + floatingBarHeight + 8`. When the floating-preview geometry observer stalls, `floatingBarHeight` is 0 while the preview is visibly mounted, so the last message sits under the preview + input bar and the overlay intercepts taps. Device log shows the correct inset is 223.67 = 115.67 inputBar + ~100 preview + 8, so the floor is 100, not 68.
- Branch `fix/ios15-retry-hit` from clean `b363866`; commit `762a178`. Probe `scripts/ios15-retry-probe/ProbeApp.swift` + `run_ios15_retry_probe.sh`: short (20pt) and natural (64pt) estimates on pinned iOS26.2, verifying the Retry point stays hittable inside the hosting tree at both estimates. Contract tests in `scripts/test_ios15_retry_hit.py` ban a bottom constraint on host.view and pin the inset floor.
- First two probe failures were **probe click-point errors, not fix regressions**: the first clicked at `bounds.maxY-2` (below the capsule), the second gated the touch through an outer container (mimicking the outer cell layer, which the inset floor addresses separately). After correcting both, the probe passed.
- Final run `35450107512` completed/success in 24m23s: retry probe, input prompt gates, SF symbol gates, 15/16 type checks, full App build/package all green. Nonempty app log, zero error lines, no incompatible-arm64e/newer-iOS link warning.
- Delivered IPA: 84,230,179 bytes, SHA256 `16e83390aba07150b0c29d8a89e68f43fe97485c96e7384ad39e26b2024b4b62`. ZIP CRC, manifest/hash, minimum15.0, Share retained, Widget/FileProvider removed. Product binary contains `CompatTextInputAlert`, `InputPromptLifecycle`, `RequestReasoningDiagnostics`, `CompatSystemSymbol` and `effectiveFloatingHeight`; Mach-O min15.0.0, SDK26.2.0, UUID `91f680c7-a450-3b39-a0a4-372604058c1b`.
- Weixin delivery succeeded as `Minis-1.13-ios15-retry-hit-762a178.ipa`, message id `openclaw-weixin:1789831232702-d4573e76`. Evidence: workspace `reports/openminis-ios15/retry-probe/run-35450107512-7nat5E/verification.json` and sibling native/audit/build logs. Do not resend or rerun completed jobs.
- Device acceptance pending: can the red Retry be tapped, and does text still paint below the input bar. Thinking-level concern is closed (title-generation request misread). Source of truth: `docs/ios15-retry-hit-investigation.md` / `docs/progress.md`.
