# Tool-card geometry: screenshot acceptance and native feedback loop

## Baseline / scope

- Source base: `ed8393eb4333989d907b6b80eb5f280b4d03fb4e`, currently `fix/ios15-cell-hit-region`; preceding dirty files are the main reviewer's diagnostic documentation only.
- User screenshot SHA256: `417a38ec414fa5bc6354b6a8aee084e023b74a2097ea94aeac9c35af8770f3c2`,1280×2769. Private original stays outside the repository; do not upload conversation screenshots or log bodies to GitHub.
- **User clarified the exact new bug:** the two compact tool records are separated by a very large blank vertical gap. This is the sole acceptance target for this task. The earlier assistant additionally treated the floating terminal preview as a reported bug; that expanded the scope incorrectly. Do not change the preview/overlay/inset as part of this fix without separate evidence and need. No error/retry control is visible in this image.
- Screenshot clock is07:53:10, preceding the available runtime-log interval07:53:31.464–07:53:55.922. The1125pt shift in that later log is a related lead, not a timestamp match or proof of this screenshot's cause.

This stage builds a **feedback loop**, not a speculative production fix. Scope is the blank space between tool rows. Preview occlusion is not a user-confirmed task here; the old Retry problem is also out of scope.

## Read set / owners

- `AssistantBlockView.swift`, especially the actual `ToolCapsuleView`, whose visible capsule is explicitly36pt tall. Keep its context menu, alerts, observed state and active/completed branches where feasible; removing those could remove the failure.
- `CollectionViewMessageListV3.swift`: block wrapper/configuration/estimate owner and bottom overlay inset owner.
- `MessageListInfrastructure.swift`: actual SelfSizingCell, generation check, reuse/reset, native/legacy hosting selection and asynchronous measured-size callback.
- `LegacyHostingContent.swift`: real UIHostingController wrapper, ideal measurement and callback coalescing.
- `MessageListLayout.swift`: actual collection layout, caches and deferred-height application.
- `ToolLiveSheet.swift` / `AIChatView.swift`: floating thumbnail, status bar and outer measurement owner. The thumbnail/status overlap within their own bottom-leading ZStack is deliberate; distinguish that from covering transcript content.
- Existing `MessageListTestView` / `SessionDataSimulator` / UITests and retry probe are candidates, not assumed working entrypoints. Current source search finds `--uitest-message-list` only in the view and UITest, not an app-root route. Existing HierarchyProbeApp positions standalone cells manually and cannot validate this bug.

## Question / success / failure

Question: do real production cells/layout/legacy hosts settle compact tool content to its actual visible height after content changes and reuse, or can a tall earlier content measurement continue reserving a large blank row?

Success for the feedback loop (not production acceptance): a command runs on pinned Apple runtime, records complete fresh per-item geometry and screenshots, distinguishes no-repro baseline from an observed oversized row, and rejects a deliberate callback/measurement negative control.

Independent failure signals: missing item/marker/callback records; stale result from a prior run; geometry sampled only through the suspect callback; forced legacy mode silently using native hosting; hand-written row coordinates/heights creating the alleged bug; stripping the capsule's view modifiers; false success from test compile failure; producing an IPA or changing production geometry before a red-capable runtime test exists.

## Minimum candidate matrix (requires independent seam review)

1. Use an actual UIWindow → view controller → UICollectionView → production layout → real SelfSizingCell → production legacy host. Mount the real ToolCapsuleView or explicitly disclose any unavailable wrapper/model substitution.
2. Plain initial load of three compact completed/running tools; update running output/status without changing intended36pt capsule height.
3. Populate real tall text content, then reconfigure/reuse the same cells for compact tools. Obtain tall measurements from rendered content, never inject1161 as the alleged reproduction.
4. Scroll away and back, reconfigure during an active deferral window, then apply the real deferred-height consumer. Track stable item IDs and configuration generations; index alone is insufficient.
5. Compare native and explicitly forced-legacy hosting. Pinned Xcode26.2/17C52 + iOS26.2 simulator; API floor/real iOS15 behavior remains outside this test.
6. Negative control deliberately suppresses size delivery or retains a stale measurement in a **test-build copy**. It validates the oracle only; it must not be reported as reproduction on unchanged source.

Oracle: sampled public UIView marker/frame and rendered screenshots must agree with cell/layout attributes. Missing samples fail. A compact capsule cannot reserve hundreds of points after a bounded settling period. Observe initial transients separately from persistent failures. Record all row frames/adjacent gaps and any real reuse, not only the first item. Use unique output run IDs and a new simulator app data directory to reject stale artifacts.

## Evidence and boundaries

No local Swift/Xcode/iOS runtime. Native execution must be through the already-authorized fork's Actions, on a new diagnostic branch with pinned tools. Do not alter upstream or main, and do not repeat the full app/IPA build just for this first geometry question.

Before any production change: run unchanged-source candidate, prove the oracle can fail, minimize any baseline failure, and only then rank falsifiable causes. If the bounded fixture stays green, report **not reproduced** and escalate to the real coordinator/app-hosted scene instead of declaring the user issue fixed.

## Preliminary component-contract gate

Independent seam review returned. Main-review correction: the production ToolCapsuleView always lays out its visible pill at36pt. Changing shell output from many lines to three lines does **not** implement expansion/collapse of that capsule. Do not use the proposed long-output→short-output mutation as proof that a visible tool row is expected to shrink. Also, the existing UITest launch flag lacks an app-root route in this checkout. A faithful full-list/page fixture needs additional work and remains the UI acceptance gate.

Before that larger build, run a much smaller **component contract** on the real Apple runtime: the actual `LegacyHostingContentView.contentSizeChanged` method is fed single, duplicate, grow/shrink-in-one-turn and root-replacement samples. The generated test copy widens only that method's access; dispatch, storage, deduplication, current configuration and callbacks remain unmodified. Test samples48/72/36/96 are synthetic inputs, not injected screenshot row frames or replayed1161→36 layout values.

Success: latest valid size is delivered after coalescing; an old-root measurement is never relabeled as the new configuration. Single/duplicate controls must work. Missing reports, wrong nonce/runtime, incomplete matrix, or failed controls make the run invalid, not a semantic red. Six cases repeated three times establish the contract signal.

Files: `scripts/prepare_ios15_size_delivery_probe.py`, `scripts/test_ios15_size_delivery_probe.py`, `scripts/ios15-size-delivery-probe/ProbeApp.swift`, `scripts/run_ios15_size_delivery_probe.sh`, and the dedicated `ios15-size-delivery-probe.yml` workflow. The diagnostic branch is `diagnostics/ios15-tool-card-geometry`; this workflow does not build or publish an IPA.

Important: even a red component test establishes only a callback/ownership defect, **not that it caused the screenshot**, and a green result is not UI acceptance. No production geometry change is authorized by this test alone.

Preflight review completed. Its concern that the baseline may be red is expected for this diagnostic contract, not a requirement to make the old behavior pass. Main resolved the actionable items: explicit main-queue barriers instead of30ms sleep, complete case/repetition identity validation, contradictory-summary rejection, and a fresh/empty evidence directory with no merge of stale results. Generator/reader tests are distinct from native behavior tests. Node YAML validation used the already-installed OpenClaw dependency, without installing packages.

Checkpoint: preliminary contract probe is ready for native execution. The initial baseline may be semantically red; that remains a component finding, not the screenshot root cause. No production patch or package.
