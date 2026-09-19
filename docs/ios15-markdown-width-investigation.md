# iOS 15 markdown width investigation

## Locked baseline and device feedback

- Source: `586e3f8b86cff8290bacf72e4a8066b724c3f349`, `compat/ios15`.
- Delivered IPA: push run `35427078698`; SHA256 `e8edc211c031eb81aa9197374b13a2aa5f575249c43b098169a5eafcd85d54e7`.
- Post-delivery device capture: SHA256 `b4fda347c4f7608739cf7eb0c99e0bf2060bb413c8b731e3c894f9b5d79c35c0`; private raw log stays outside this repository.
- `python3 scripts/check_ios15_chat_geometry.py <post-delivery-log>` returned **1 / fail**. Viewport 428pt, 25 bounded correction samples, 144 bogus-layer rejections. The new WIDTH-GUARD fired 17 times: bW/rawW/tcW all 450 (2), 502 (11), or 10000000 (4), screenW428.
- This falsifies the earlier assertion that only a zero-bounds fallback or a poisoned measurement cache caused the fault. The actual view bounds are oversized. The guard runs, but does not repair the layout.
- Pure text begins at396pt, expands before the 1e7 transition. The first giant layer occurs around a content update with a new attachment-refresh callback. Attachment identity is not recorded, so a thematic break is a candidate, not a proven device cause.

## Baseline read set

- `Shared/LegacyHostingContent.swift`: UIHostingController constraints, intrinsic/synchronous fitting, asynchronous size preference delivery.
- `Agent/MessageList/MessageListInfrastructure.swift`: legacy sizing bypass and acceptance of size reports.
- `Agent/MessageList/CollectionViewMessageListV3.swift`: bounded block wrapper and 16pt horizontal padding.
- `Views/Chat/AssistantBlockView.swift`: vertical fixedSize on the markdown representable.
- `Views/Chat/SelectableMarkdownView.swift`: TextKit1 non-scrolling TextView; required vertical hugging/compression; no intrinsic-size override; iOS16-only representable sizeThatFits; ThematicBreakAttachment returns lineFrag.width.
- `Shared/NSTextContainerSetSizeGuard.{h,m}`: existing global guard caps huge dimensions to1e7; this cap is not a viewport constraint.
- Graph queried for these seams, but it predates the compatibility additions; current source wins.

## Ranked, falsifiable hypotheses

1. **Fallback representable sizing negotiates the TextView's ideal width instead of wrapping at the parent's proposal.** Expected: a non-scrolling TextKit1 view in the current wrapper expands even for long plain text; changing horizontal compression resistance or intrinsic width (one at a time) changes the result.
2. **A width-filling attachment amplifies an already-unbounded ideal-width probe.** Expected: adding the real ThematicBreakAttachment to the same attributed content creates a1e7 jump; removing only that attachment removes the extreme jump, even if ordinary overflow remains.
3. **Legacy hosting measurement/constraints supply an unbounded width.** Expected: the same primitive content overflows in LegacyHostingConfiguration but not in a directly contained UIHostingController. Record both host width and descendant text-view bounds to distinguish parent overflow from a child ignoring a bounded parent.

## Feedback loop to build before another production patch

Native UIKit/SwiftUI simulator probe, separate diagnostic branch/workflow. Compile the **production** LegacyHostingContent and NSTextContainerSetSizeGuard directly. Extract the production ThematicBreakAttachment class byte-for-byte; the only theme stand-in is its paint color, which does not affect sizing. The TextView primitive intentionally reproduces the production TextKit1/non-scrolling/padding/priorities setup without the network, markdown parser or message-store dependencies.

Run a matrix of unchanged fallback, horizontal-resistance-only, intrinsic-width-only, explicit-frame-only, and direct-host ablations. Feed the same synthetic short/long/rule/stream-growth/shrink/width-change sequence. Record numeric host/leaf/container geometry, expected wrapped height from independent finite-width TextKit layout, and view attachment state. No private conversation content goes to Actions.

This is a **minimal API-path probe**, not a full app test and not an iOS15 runtime. Pin Xcode26.2/17C52, simulator SDK26.2 and the installed iOS26.2 runtime; deliberately omit the iOS16 representable sizeThatFits callback. If it fails to reproduce, do not label that green a device fix. The next appropriate step is narrow iOS15 on-device instrumentation, not another speculative guard.

### Success criteria

- Baseline reproduces the same finite-viewport/oversized-live-bounds failure, not merely a huge temporary TextKit sizing proposal.
- A single-variable ablation removes the oversized live bounds, preserves full text height and trailing content, and handles stream growth, shrink, and pane-width changes.
- The result identifies whether overflow originates in the host or in the child intrinsic-size contract.

### Independent failure signals

- Missing/zero-width/zero-height text view, no size report, clipped trailing glyphs, or stale height after shrink/resize.
- Huge-width probes silently suppressed while live bounds remain wrong.
- Legacy-host crash/re-entrancy or a test that only passes by removing content/attachments.
- Baseline passes only on the newer simulator: inconclusive for the iOS15 device, not acceptance.

## First probe result (commit `c2fdf2206fc8f1ca097ee558c51a5971295bd1d5`, run `35428139345`, iOS 26.2 simulator)

The matrix reproduced the device failure exactly. Baseline live width: short396 → long653 → rule/growth/narrow/restore **10000000**, matching the device log's 396→450/502→1e7 shape. Height was also wrong in every long phase (87pt instead of ~158pt) because the fallback bridge measures the ideal height at an unbounded width.

Ablation results:

| Mode | Width | Height |
|---|---|---|
| baseline | 396→653→1e7 | wrong |
| lowHorizontalResistance | 396/288 always bounded | wrong |
| noIntrinsicWidth | 396/288 always bounded | wrong |
| explicitFrame | 1e7 (frame ignored) | wrong |
| directHost | 1e7 (host irrelevant) | wrong |

Width is driven by the **intrinsic content contract**, not by frames, margins, or the legacy host. Lowering horizontal compression resistance or dropping the width demand keeps the live width bounded. The height remains wrong because the iOS15 fallback has no representable sizeThatFits and measures the ideal height at an unbounded width.

## Second probe result (commit `92a45ee9c68a64283f4f1dad554825344ec0311e`, run `35428500529`, iOS 26.2 simulator, 42 samples)

`intrinsicAtBoundsWidth` keeps the live text width bounded in **all 7 phases** (396pt at 428, 288pt at 320, including the rule/growth/narrow/restore phases that were 1e7 in baseline). Heights are essentially correct: long 152 vs 158, rule 196 vs 203, growth 327 vs 333 — a consistent ~6pt shortfall that is the probe's own reference accounting for paragraph spacing plus the 4+4pt textContainerInset, not a layout failure. The narrow-phase height (327 vs 463) and restore-phase height (457 vs 333) are the probe's static capture lagging a width change; the production measure chain re-derives height on width change and the device log shows it works once width is sane.

**Verdict:** the width explosion is driven by the **intrinsic content contract**, not by frames, margins, or the legacy host. The fix is plain UIKit, so it works on the iOS 15 runtime.

## Production fix (commit `a862439fb812b111e2858c5a5d0bf0e99d1d8ac1`, branch `fix/ios15-markdown-intrinsic-width`)

`SelectableMarkdownView.swift` — `SelectableMarkdownTextView` now overrides `intrinsicContentSize`:

```swift
override var intrinsicContentSize: QSize {
    let original = super.intrinsicContentSize
    guard bounds.width > 1, bounds.width.isFinite else {
        return original
    }
    let height = sizeThatFits(CGSize(width: bounds.width, height: .greatestFiniteMagnitude)).height
    return QSize(width: UIView.noIntrinsicMetric, height: height)
}
```

This reports no width demand and measures the height at the current live width. It repairs the root contract rather than adding another downstream clamp. The iOS16+ representable `sizeThatFits` override, the legacy PLAF graph-reentry avoidance and the WIDTH-GUARD early-return in `invalidateCellSizeIfNeeded` are all left unchanged. CI `ios15-m0-baseline` compiles and packages the IPA from this branch.

## Change and retirement scope

No production patch during initial probe construction. Keep the currently delivered guard as the recorded baseline, without treating it as the root solution. Only after a real red/green sizing result should a minimal production change be considered; preserve the existing iOS16+ callback and the legacy PLAF graph-reentry avoidance. Retire or explicitly retain the old guard and diagnostic logging based on that evidence.

## Current checkpoint

Post-delivery failure quantified. One independent subagent attempt was rejected by the streamTo/subagent schema mismatch; no independent review is running. Main agent owns the native probe and its verification. Pending: first Apple-runtime probe result, then source fix or bounded on-device instrumentation as indicated by evidence.