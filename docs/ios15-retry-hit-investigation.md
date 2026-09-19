# iOS 15 chat Retry hit-test and composer-overlap investigation

## Locked baseline and user evidence

- Baseline product source `889af8d99006a6a65a6da3a2424ac8d25f14e3b1` (branch HEAD `b36386694fd77b11cf309101b0d3b6c0bcabc893`), clean worktree checked before branch `fix/ios15-retry-hit`.
- User repeated report: in-chat red error area Retry is untappable; message text renders below the input bar and cannot be clicked. Screenshot `7c49d909-7504-4060-9c1e-a74d519a4b06.jpg`, SHA256 `b0deaa67c9d43fe4280a102144ef0a1c89a051699d191e41b345b1cb02784285`. Thinking-level concern is closed (title-generation misunderstanding).
- The red error footer is `BridgedAssistantFooterV3`/`inlineError` (Retry capsule, right side) hosted through the iOS 15 `LegacyHostingConfiguration` path. The composed overlay (floating tool preview + input bar) sits above the message list.

## Baseline read set

- `Shared/LegacyHostingContent.swift`: content view pins `host.view` top/leading/trailing only; intrinsic/systemLayoutSizeFitting measure at live width; async GeometryReader size report.
- `Agent/MessageList/MessageListInfrastructure.swift` (`SelfSizingCell`): iOS15 uses `LegacyHostingConfiguration`, clip-to-bounds content view, cached height and legacy measured size path.
- `Views/Chat/ChatMessageViews.swift` + `CollectionViewMessageListV3.swift`: footer `inlineError` Retry, `BridgedAssistantFooterV3`, bottom inset `inputBarHeight + floatingBarHeight + 8`.
- `Views/Chat/AIChatView.swift`: `floatingToolPreview` in the composer overlay; `floatingBarHeight` from `compatOnGeometryChange`; the overlay stack is a ZStack over the message list.

## Hypotheses (each with a prediction)

1. Hosting-view overflow is touch-dead. UIKit hitTest checks the receiver's bounds before subviews, so the unpinned tail of `host.view` (where the Retry capsule sits) below a short cell estimate is unreachable. Prediction: on the pinned simulator, with a 396x40 estimate, the window hit at the trailing-bottom of the content returns nil / not inside the hosting tree; at the natural height it returns inside.
2. Unreported floating preview height under-insets the list. If `floatingBarHeight` stays 0 while a tool preview is visibly mounted, `contentInset.bottom` is short by the preview height, so the last message sits under the preview + input bar and the overlay intercepts taps. Prediction: `effectiveFloating` floor keeps the inset correct even before the observer fires.
3. Retry callback gate. `bridge.onRetry` is only assigned for user messages or "last" assistant rows while `!vm.isProcessing && !vm.isCompacting`. Prediction: when those conditions are false the Retry cell is not rendered at all — a visible-but-inert button is a different symptom and would still be directly testable; the earlier log shows at least one successful retry flow, so the callback itself works when reachable.

## Implementation (this branch)

- `LegacyHostingContent.swift`: added `override func hitTest` that uses the default path first, then forwards touches inside `host.view.bounds` even below our own bounds. No bottom constraint added (intrinsic sizing is load-bearing).
- `CollectionViewMessageListV3.swift`: bottom inset uses `Self.effectiveFloatingHeight(floatingBarHeight:hasToolBlocks:)`, which returns the reported height when >1 and floors to `hasToolBlocks ? 100 : 0` when the observer has not reported yet. The 100pt floor is the measured preview height from device logs (223.67 inset = 115.67 inputBar + ~100 preview + 8 spacing); the earlier 68pt guess was too small. The helper is a pure seam for structural tests.
- Probe `scripts/ios15-retry-probe/ProbeApp.swift` + `scripts/run_ios15_retry_probe.sh`: compile production `LegacyHostingContent` with a footer-mirroring SwiftUI view on the pinned iOS 26.2 simulator; measure overflow and window hit results at short (20pt, button center below bounds) and natural (64pt) estimates. Struct/test contracts in `scripts/test_ios15_retry_hit.py`; workflow step wired.

## Verification / independent failure signals

- Pinned workflow executes the probe; green requires the Retry point to hit inside the hosting tree at BOTH short and natural estimates, with overflow reported numerically.
- Structural tests ban a bottom constraint on host.view (preserves intrinsic sizing) and pin the inset floor.
- Failure signals independent of success: dead zone returns nil at the short estimate, the floor makes the inset overshoot/undershoot, the hitTest change routes non-host touches oddly, or the probe's host.view lookup (`subviews.first`) stops matching.
- Templates are a production-path probe; no iOS 15 runtime or device here. Device visual acceptance of the Retry button and under-input overlap remains pending after delivery.

## Final evidence

Run `35450107512` (source `762a178f36db52690f727dc1f3a1ab42f5534bfb`) completed/success in 24m23s. All gates passed: retry hit-test probe, input prompt gates, SF symbol gates, 15/16 type checks, full App build/package. Nonempty app log, zero error lines, no incompatible-arm64e/newer-iOS link warning.

Probe result: at a 20pt estimate the footer overflows the content view by 30.33pt and the Retry point still hits inside the hosting tree (`_UIHostingView<AnyView>`); at the natural 64pt estimate it also hits inside. The earlier two probe failures were click-point errors in the harness, not fix regressions.

IPA delivered as `Minis-1.13-ios15-retry-hit-762a178.ipa`, 84,230,179 bytes, SHA256 `16e83390aba07150b0c29d8a89e68f43fe97485c96e7384ad39e26b2024b4b62`. ZIP CRC, manifest/hash, minimum15.0, Share retained, Widget/FileProvider removed. Product binary contains `CompatTextInputAlert`, `InputPromptLifecycle`, `RequestReasoningDiagnostics`, `CompatSystemSymbol` and `effectiveFloatingHeight`; Mach-O min15.0.0, SDK26.2.0, UUID `91f680c7-a450-3b39-a0a4-372604058c1b`.

This delivery does not claim an effort-policy fix. Device visual acceptance of the Retry button and under-input overlap remains pending.

## Checkpoint

Working tree was clean at `b363866`; branch `fix/ios15-retry-hit` created; fix + probe + tests + workflow are this change set. The pinned probe and full package build passed before delivery. Do not treat the earlier successful retry() log as proof the visible button was reachable; device acceptance remains pending.
## Full-hierarchy probe (run `35453402726`, source `9761f4814150d902314e817e800740c026c4fa72`)

Added `scripts/ios15-retry-probe/HierarchyProbeApp.swift` and extended the runner to build/run two probes on the pinned iOS26.2 simulator:
1. unit probe (content view in isolation): 20pt estimate → Retry center hits inside the hosting tree; 64pt control also hits.
2. full-hierarchy probe: footer cell (legacy hosting) + bottom-anchored 180pt composer overlay above it in z-order, mirroring AIChatView.

Full-hierarchy results (all asserted):
- Phase A control (no overlay): Retry point hits inside the cell hosting tree — `true`.
- Phase B short-inset (cell under overlay, i.e. floating-preview geometry stall): Retry point hits the OVERLAY and not the cell — `true` — the user's exact symptom reproduced.
- Phase C fixed-inset (cell above the same overlay, the `effectiveFloatingHeight` floor): Retry point hits inside the cell again and not the overlay — `true`.

Run `35453402726` completed/success. The inset-floor fix is now validated at the full hierarchy level, not just the content view in isolation. Device acceptance still pending.
