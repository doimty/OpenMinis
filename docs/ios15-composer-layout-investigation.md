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

## Run 35490371431 — probe went red on the real production path

- Both composer runs are **completed/failure**, not still running. Run `35490371431` is the second attempt after fixing `await` on the async diffable data source apply.
- **Mutation was run**: `mutant-zero-grid` (attachment height forced to 0) correctly rejected as invisible. This proves the oracle can go red on a real regression.
- The useful result is the positive/negative matrix, not the mutation:

| flow | geometry | single-chip grid height | verdict |
|---|---|---|---|
| native FlowLayout | native | 70pt | ✅ |
| native FlowLayout | legacy | 70pt | ✅ |
| **LegacyFlowLayout** | **legacy** | **6pt** (64pt chip pushed under the input field) | ❌ |
| **LegacyFlowLayout** | **native** | **6pt** | ❌ |

- **Root cause narrowed to `LegacyFlowLayout` itself**, not the bottom inset, not the hit-test path. The 64pt chip renders fine but the flow reports a 6pt height (padding-only strip), so the draft image is hidden under the input field — exactly the user's screenshot.
- Collection side is also red across all three phases: cell stays at the 40pt estimate, host content is 96pt, `cached_count=0`, `preferred_calls=6`. The async measured height is not writing back into the layout cache. This is a separate chain from the attachment one.

## Fix candidate implemented (unverified, needs probe rerun)

- `src/ios/Shared/LegacyFlowLayout.swift`:
  - Child measurement moved from `.background(GeometryReader)` to `.overlay(GeometryReader)`. A background reader sits behind the hosted view and on some legacy layout passes reports the parent proposal (0 height inside a 0-high ZStack) instead of the child's fixedSize ideal size, so the first size report arrives as `.zero` and the height feedback never converges past the padding-only strip.
  - Height cache reset on content-id set change (`.onChange(of: items.map(\.id))`) so add/remove/reorder cannot leave a stale tall height behind while the new content's first reports are still zero-size.
- `scripts/ios15-composer-probe/ComposerFixture.swift.in`: recorder now seeds from the initial height on `onAppear`, so a positive seed that already equals the settled measurement no longer reports zero.
- `scripts/ios15-composer-probe/ProbeApp.swift`: multi-item oracle now asserts **all** chips, not just the first one.
- Contract tests: `test_ios15_legacy_flow_measures_via_overlay_not_background` added to `test_ios15_compat_contract.py` (19/19); `test_ios15_composer_probe.py` updated (3/3).

**This is a candidate, not a delivered fix.** The probe is on a forced-legacy iOS26.2 simulator; it can narrow the cause but cannot stand in for iOS15.1.1 device acceptance. The collection-side red is still open and is a different chain from the attachment one.

## Second fix candidate — remove the legacy height feedback loop

The first candidate fixed child measurement (`background` → `overlay`) but the probe still showed a 64pt chip rendered from a 6pt grid frame. The direct geometry probe then showed the mismatch was real in the layout model: the child arrangement reported 70pt while the outer `LegacyFlowLayout` frame stayed at 0pt plus the 6pt input-grid padding.

The next candidate in `src/ios/Shared/LegacyFlowLayout.swift`:

- removes `LegacyFlowHeightKey` and the `@State height` / `.frame(height:)` feedback loop;
- puts a transparent spacer with `height: arrangement.height` inside the `ZStack`, making the calculated rows part of the container's natural layout size;
- keeps child measurement on `.overlay`;
- measures only the available width and re-packs when that width arrives;
- clears item-size measurements when IDs change, without resetting an independent height state.

The probe oracle was also corrected: the previous `probeFrame` used the iOS 15 preference adapter and could report a pre-offset proposal. On the pinned iOS 26.2 runner it now uses native `onGeometryChange`, keeps a bounded frame-history summary, and checks the actual final frame. The prior screenshots were visually healthy but the numeric frame evidence was stale, so they cannot be used as proof of the old candidate.

This candidate is now being re-run. It is not a delivered iOS 15 fix until the composer matrix is green and a real iOS 15.1.1 device accepts the layout.

## Same-pattern scan — other UI measurement feedback loops

Scanned for `.background(GeometryReader)` + preference + state height feedback, the same class as the LegacyFlowLayout failure:

| Site | Pattern | Risk | Why |
|---|---|---|---|
| `LegacyHostingContent.swift:37` `LegacyHostedSizeKey` | `.fixedSize(vertical:true)` + `.background(GeometryReader)` | 🔴 **high** | Same shape as LegacyFlowLayout. The cell's async height callback writes the measured size back into the layout. If the background reader reports the cell's current estimate (40pt) instead of the content's ideal height (96pt), the collection never converges — this is the most likely cause of the probe's collection-initial/grow/shrink all staying red. **Fixed to `.overlay` in the same commit.** |
| `InlineVoiceInputView.swift:802` `TranscriptContentHeightKey` | `.fixedSize(vertical:true)` + `.background(GeometryReader)` | 🟡 medium | Same shape; already has a 120pt shrink collapse log and a `transcriptMinHeight` floor. Panel lifecycle only, not the attachment/collection chain. |
| `ChatMessageViews.swift:63` `PreviewContentSizeKey` | `.background(GeometryReader)` in a ScrollView | 🟢 low | Falls back to `maxCardWidth`/`maxCardHeight` caps before the first measurement, so it never collapses to zero. |
| `AIChatView.swift:3453` `TranscriptHeightKey` | `.background(GeometryReader)` in a ScrollView | 🟢 low | Clamped to `min(max(transcriptHeight, 22), 100)` — 22pt floor prevents zero collapse. |
| `AIChatView.swift:3649` `AttachmentGridHeightKey` | `.background(GeometryReader)` on the attachment ScrollView | 🟡 medium | This is the outer half of the chain already under test; its inner content (LegacyFlowLayout) is what was failing. |

The general rule: a `.background(GeometryReader)` measuring a `.fixedSize` view inside a container whose own height is still an estimate can report the estimate instead of the ideal size. Use `.overlay` when the measurement must reflect the child's real rendered size, or add an explicit non-zero floor when the parent height is genuinely unknown.

## Scope / retirement

Diagnostic branch only. No production layout, hit-test, input, model or network behavior change at this checkpoint. Do not enlarge the 100pt workaround. Its historical claim of a measured100pt toolbar is superseded by the earlier acceptance audit and the fresh65pt evidence above.
