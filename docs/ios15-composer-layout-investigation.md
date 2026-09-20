# iOS 15 composer attachment / message geometry investigation

## Baseline and acceptance boundary

- Source baseline: `01d84edbfaaf23c108f843ce5e1762951c8d1369`, clean `fix/ios15-cell-hit-region`; remote fork ref independently matched.
- New user capture: log SHA256 `5ad0659b341bdb01385c9d40190099770470ca8279bd613f8b8caa42e068e9b1`, 253648 bytes / 1500 lines, 11:09:57–11:17:01. Private original stays outside Git.
- First new screenshot: attachment thumbnail is almost entirely hidden in the composer, only a thin strip visible. This is a **draft attachment** layout case, not automatically an already-sent image/list-inset case. Second screenshot: tool thumbnail overlaps the lower message area.
- No device build identifier is recorded in this log segment. Do not assign it to `78f32a9` solely from queued-message delivery time.
- Previous `78f32a9` and `01d84ed` cloud runs passed their existing gates. Those detached/mirrored-cell hit tests do not prove production cell sizing, SwiftUI button activation, draft attachment visibility, or iOS15 device acceptance.

## Fresh evidence that changes the investigation

- Log line 16: base inset `108 -> 73`, inputBar `0`. With the production +8 gap this is a **65pt reported toolbar**, not a zero toolbar. Line 17 seeds the input bar at 115.357; line 59 reports adjusted bottom inset 188. The code's `ToolPreviewThumbnail` is 100x65 **points**, and `FloatingToolBar` is a bottom-leading ZStack with a >=38pt status bar. Pixel dimensions in a Retina crop cannot be compared directly with point dimensions.
- Source `ToolLiveSheet.swift:243-276` positively identifies the terminal thumbnail + 9/9 status strip. It is not a full ToolLiveSheet or a shell-output cell inferred from appearance alone.
- Log lines 126 and 811 record `send()` with input text `继续`, attachments=0. They are composer sends, not evidence of the orange Resume action being delivered. Generic `[SuspendState] RESUME` is scroll/UI suspension, not the Resume button.
- Existing numeric replay detector: 7 bounded live measurements, viewport428, zero rejected layers. This checks width regressions only, not attachment visibility or cell-height convergence.
- Every logged force-to-bottom snapshot has `heightCache=0`. This merits a real collection/layout test, but by itself does not prove which callback is missing.

## Phase 1: build a red-capable runtime loop before changing production

Baseline read set:
- `AIChatView.inputBar`: `attachmentGridHeight=0`, production attachment ScrollView/height preference, outer input-bar observer.
- `ChatInputBar.InputAttachmentGridView`, `LegacyFlowLayout`, `CompatGeometry`: flow packing and measurement.
- `SelfSizingCell.applyHostedContent`, `LegacyHostingContentView`, `MessageListLayout`, `NoAnimationCollectionView`, `MessageListViewController`: async measured height -> actual collection frames/cache.

One native probe command: `bash scripts/run_ios15_composer_probe.sh /absolute/output`.

The probe must use source-derived production components, not a reimplementation of the alleged fix:
1. Extract the actual composer attachment ScrollView expression and InputAttachmentGridView/drag delegate from current source. Substitute only fixed-size64 image chips/no I/O and the unrelated composer field. Force the iOS15 branch in a generated build copy, leaving production source unchanged.
2. Compile actual SelfSizingCell, NoAnimationCollectionView, MessageListViewController, MessageListLayout and LegacyHostingContent. Retain their real callbacks/cache ownership. Substitute only logger/unused Markdown types. Force legacy availability routing in the generated copy.
3. Check actual rendered chip/global frames, reported parent height and input-field overlap after empty->one->many->empty->one and narrow/wide changes. Keep a primitive-layout control and a positive-initial-height ablation to discriminate an outer zero-height bootstrap from the flow layout.
4. Check actual collection cell frames/cache and adjacent-cell overlap after initial render, growth and shrink. Do not manually call preferredLayoutAttributesFitting or supply measured heights from the test.
5. Save screenshots and numeric reports even when assertions fail. The initial production-source run may be RED; this is a diagnostic workflow, not a package delivery gate yet.

Independent failure signals: missing chip frame/height report; a64pt chip occupying a <64pt viewport or intersecting the text field; a visible hosted cell larger than its allocated cell after settling; overlapping neighbors; zero callbacks; a probe source extraction mismatch; crash/timeout.

No cause is selected until this loop runs. A green forced-legacy run on iOS26.2 does not exonerate iOS15.1.1; if the exact failure cannot be reproduced, add narrowly-scoped real-device telemetry rather than calling the problem fixed or increasing arbitrary inset floors.

## Scope / retirement

Diagnostic branch only. No production layout, hit-test, input, model or network behavior change at this checkpoint. Do not enlarge the 100pt workaround. Its historical claim of a measured100pt toolbar is superseded by the earlier acceptance audit and the fresh65pt evidence above.
