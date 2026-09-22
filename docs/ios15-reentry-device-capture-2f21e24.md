# Device capture at 2f21e24: provisional heights reach the authoritative row cache

Status: two verified LOCAL native height-write witnesses. Full re-entry acceptance remains **INCONCLUSIVE**. No new production fix, commit, build or IPA was made from this analysis.

## Baseline and provenance

- Source: `2f21e242df71d63576682f8ac61c50a097f3a5ae`, diagnostic branch `diagnostics/ios15-session-reentry`.
- Successful full device build: Actions run `35699475162`, Xcode 26.2 / 17C52, SDK 26.2, iOS deployment target 15.0.
- Delivered diagnostic IPA: 84,308,511 bytes; SHA256 `ca90f97f935db020fd11245e7d3973ce8e18f0d45593592ba22f8cd91c8d43b7`.
- Returned trace SHA256: `b6d0f8125139527a7e696951eafbc245f867f5583e2ef9af3f7499f945057134`. All 8001 records identify the expected source/run; sequence 1…8001 is intact; last record is an explicit event-cap marker. Duration: 13.598982 seconds.
- Local evidence bundle: workspace `reports/openminis-ios15/reentry-capture-693f08de/`. The trace is not checked into this repository. It contains scalar diagnostic observations, not the original conversation.
- The repeated inbound attachment/message was the same file, not a second reproduction.

## Verified local chains

These are joined through collection owner, concrete text/host/cell identities, the parent links and the active cell configuration. Host-root generation and cell configuration generation are DIFFERENT namespaces and are not equated. The examples below use a text event with a real parent cell, not an unattached event joined by temporal proximity.

### Target row 249: 381-point under-height and recovery

| Sequence | Actual observation |
| --- | --- |
| 518–519 | Cell configured for row 249, cell generation 2; existing height/estimate 1865. |
| 522 | Its text view, attached to the same collection/cell, reports `intrinsic-unset`: bounds/container width 0, height 1480. |
| 523 | Its host, root generation 4, publishes outer width 428, height 1484. |
| 524 | SAME text view/cell generation has already measured height 1861 at real width 396; its actual text-frame height is still the provisional 1480. |
| 529–530 | The older 1484 host observation is delivered and accepted by that cell. |
| 537–538 | Cell returns 1484 through `legacy-observation`; layout writes 1865 → 1484 with `decel=1`, `deferred=1`, source `pre`. |
| 747–770 | A DIFFERENT cell now represents row 249. Its text/host produce the same provisional size, then finite-width height 1861 / host height 1865. |
| 777–778 | That second cell's accepted 1865 restores the row cache 1484 → 1865. |

The two cache writes are 191.913 ms apart. This is a row-level recovery across different cell objects, NOT a same-cell recovery and NOT a measured 191.913 ms of displayed pixels.

### Target row 253: same-cell/same-configuration witness

| Sequence | Actual observation |
| --- | --- |
| 1805–1806 | Cell configured for row 253, cell generation 2; existing height 1521. |
| 1809–1810 | Its attached text view has width 0 and height 1349.333; host root generation 4 publishes 1353.333 at outer width 428. |
| 1811 | SAME text object/cell generation already measures 1516.333 at width 396; its actual text-frame height is still 1349.333. |
| 1816–1817 | Host delivers 1353.333; same cell accepts it while its bounds height is still 1521. |
| 1822–1823 | Finite intrinsic size / correct host preference 1520.333 are observed before the next self-size return. |
| 1826–1827 | The cell nevertheless returns its last DELIVERED height, rounded to 1354; layout writes 1521 → 1354. |
| 1833 | A subsequent actual cell return observes `frameH=1354`; this is not only a requested fitting size. |
| 1835–1841 | Root generation 5 publishes and delivers 1520.333 to the same cell generation 2. |
| 1848–1851 | Cell returns 1521, layout writes it back, and a subsequent return observes `frameH=1521`. |

The two cache writes are 14.188 ms apart. The same cell/configuration/text object is retained. Text length is unchanged; the trace does not contain a content hash, so length equality must not be described as byte-for-byte content identity.

## Source attribution

1. `SelectableMarkdownView.swift:5223–5246`: with unset bounds, `intrinsicContentSize` deliberately returns no intrinsic WIDTH but preserves `super.intrinsicContentSize.height`. The observed 1480 / 1349.333 come from this exact instrumented branch. These provisional height estimates must not be confused with a finite-width result.
2. `LegacyHostingContent.swift:contentSizeChanged`: the outer host width is already finite (428), so the provisional descendant result satisfies the existing outer-size/generation checks. A finite OUTER width is not proof that the descendant text was measured at a finite width. The asynchronous, same-generation delivery does not carry readiness/provenance.
3. `MessageListInfrastructure.swift:applyHostedContent` (around 195–219): any accepted callback clears cached/seeded height and stores `legacyMeasuredSize`. `preferredLayoutAttributesFitting` (around 504–536) rounds that delivered value and returns it. Row 253 demonstrates a correct preference arriving before the bad cell return but after the preceding asynchronous delivery; this is not proof that the latest-wins queue fix is absent.
4. `MessageListLayout.swift:shouldInvalidateLayout` / `invalidationContext` (around 399–516): the browse-mode shrink guard only considers `heightCache`, not `precalcHeights`. Source `pre` means a precomputed value exists but no authoritative height-cache entry exists, so the first fitting result is allowed through. `invalidationContext` then really writes the smaller value. The logged `deferred=1` is the MODE FLAG, not evidence that this particular write was deferred.
5. The correction path around `SelectableMarkdownView.swift:7202–7257` can postpone committing a real text correction while browsing. The trace proves the finite result was computed before the bad delivery, but it does not record every internal defer/escape decision. Do not over-attribute that scheduling detail.

The established local defect is promotion of an unset-width text estimate into an authoritative row height, overwriting a height that agrees with the later finite-width measurement. The first problem is not absence of a correct numerical measurement: that measurement already exists before the provisional value is consumed. Do not replace this finding with a general scroll-position feature or revert the accepted width/queue-generation fixes.

## Trace budget and full-acceptance limits

- 6805 / 8001 events (85.05%) are host-size events: 3437 preferences, 3272 deliveries, 96 cell acceptances.
- All host-size events have one of the two real collection owners, not `owner=none`. Owner presence does NOT prove visibility. The host events lack a visibility/rejection-reason field, so neither “all offscreen noise” nor “all visible accepted updates” is justified.
- Many unchanged-height reports have increasing root generations and `previousH=0`. `LegacyHostingContentView.configuration` unconditionally calls `updateRoot`, which increments the root generation and clears `lastSize`. This is a concrete deduplication defeat mechanism; the trace does not identify every caller of that setter. Quantified CPU/perceptual cost requires separate evidence.
- The capture shows the target 255-item session entered once, browsed, and left; another 39-item session then opens. It reaches the cap before a recorded return to the original session. Therefore this file does NOT cover the complete requested repeated-entry scenario.
- Whole-file analyzer and hardware-loop verdicts remain `INCONCLUSIVE` (exit 1). The user supplied no visual caption: `userObservedJump=unknown`. No absence of visual jitter may be inferred.
- No screenshot/frame-presentation timestamps, complete content hashes, or post-fix controls are present. This is not a device fix acceptance, even though the local cache/frame under-height is directly observed.

## Evidence checks run

- Existing `analyze_trace.py log … --expected-commit 2f21e24…` and `hardware_loop.sh … unknown …`: 8001 valid events, explicit cap retained, `INCONCLUSIVE`, no parsing errors.
- Local `verify_capture_witness.py <original trace> --output <report>`: both ownership-correlated local chains confirmed. Ten intentionally wrong owner/parent/host/generation controls are rejected. Height values are related through observed data and rounding, not used as hardcoded expected UI answers.
- That checker validates historical evidence ONLY. Re-running an old trace cannot validate a new implementation and must never be advertised as a native red/green regression test.

Read-only cross-check independently confirmed the source, sequences, parent chains, host counts and visibility limits. Its broad phrase that the value was not proved adopted by a cell is narrowed by the main review: `cell-accepted` plus the source assignment, `legacy-observation`, the actual layout write and subsequent `frameH` prove local adoption. The provisional value also appears in the text view's actual frame at sequences 524/1811. None of this proves screen presentation, complete original re-entry causality, or that one proposed fix is sufficient. No blanket review-pass claim is made.

Next validation boundary: distinguish provisional descendant measurement from a trustworthy same-content/width report, then test any candidate against the real renderer/host/cell/layout sequence and legitimate growth/shrink/reuse controls. Improve telemetry budgeting so repeated unchanged host-generation traffic cannot remove the repeated-entry portion. No candidate has yet passed that boundary.
