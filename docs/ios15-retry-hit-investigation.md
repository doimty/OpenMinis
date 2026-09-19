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
- `CollectionViewMessageListV3.swift`: bottom inset uses `effectiveFloating = floatingBarHeight > 1 ? floatingBarHeight : (hasToolBlocks ? 68 : 0)`. Pure defensive floor; the VM already exposes the tool-block predicate.
- Probe `scripts/ios15-retry-probe/ProbeApp.swift` + `scripts/run_ios15_retry_probe.sh`: compile production `LegacyHostingContent` with a footer-mirroring SwiftUI view on the pinned iOS 26.2 simulator; measure overflow and window hit results at short (40pt) and natural (96pt) estimates. Struct/test contracts in `scripts/test_ios15_retry_hit.py`; workflow step wired.

## Verification / independent failure signals

- Pinned workflow executes the probe; green requires the Retry point to hit inside the hosting tree at BOTH short and natural estimates, with overflow reported numerically.
- Structural tests ban a bottom constraint on host.view (preserves intrinsic sizing) and pin the inset floor.
- Failure signals independent of success: dead zone returns nil at the short estimate, the floor makes the inset overshoot/undershoot, the hitTest change routes non-host touches oddly, or the probe's host.view lookup (`subviews.first`) stops matching.
- Templates are a production-path probe; no iOS 15 runtime or device here. Device visual acceptance of the Retry button and under-input overlap remains pending after delivery.

## Checkpoint

Working tree was clean at `b363866`; branch created; fix + probe + tests + workflow are this change set. Cloud compilation and probe execution pending. Do not claim a device fix before the pinned probe and package build pass, and do not treat the earlier successful retry() log as proof the visible button was reachable.