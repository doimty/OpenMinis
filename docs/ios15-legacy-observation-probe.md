# Legacy asynchronous observation/cache experiment

Baseline production2acd561e66d73c1ed15424d6e6d6208a68b48a57; new diagnostic branchdiagnostics/ios15-legacy-observation. No production UI changes in this step.

## Device source of truth

Capture974a879d/SHAb2cec4f0… saved one252-node tree despite inspect argument failure. It reports textcell/Legacyheight32 vs inner host79.333, precedingblock419 vs561.333 andfooter4 vs54.333. Normal tool/user/header controls match. These are frame observations, not proof of clipping, persistent pixel overlap or a unique callback loss.

The companion log1fb741a4/SHA c6fd9ec6a9a141b0395617ab8b0a49b02c83a3b9397bf6a98677b8760d6aed96 covers15:36:52–15:38:02, the same V2 run. At15:37:03 it reports the text's86-character rendered storage measured at75.3pt/width396, repeatedly skipped by the dedup fingerprint. At15:37:06 the tree sees75.333+4pt host content but32pt outer row. This strengthens the transfer/cache boundary, not a theory that the text was never measured.

The fresh snapshot contains222 items/209 block items/12 messages, with last6 blocks and a tail footer. The latest IDs differ from older logs: text8346D728, shell9597AC21, files699A4922/2FD2045F/731222BC. Never reuse old UUIDs across regenerated message objects. No V3 execution is recorded in this log.

## Hypotheses and experiment

PriorityC1: clearCachedHeight discards legacyMeasuredSize even though the producer deduplicates equal size. C2: an obsolete seededHeight survives generic clear and wins before the legacy sizing branch. Alternatives: first report rejected by window/generation or same-size reconfiguration failing to emit another edge. Accepted observations can also fail to cause another preferred-layout pass.

Use real production MessageListInfrastructure, MessageListLayout, LegacyHostingContent and LegacyFlowLayout in an isolated UIKit app, forcing only the legacy hosting route on fixed iOS26.2/Xcode26.2/17C52. Stand-ins are restricted to unrelated logger/Markdown/table classes; row content is an explicit fixed-height SwiftUI model. Read-only state accessors live only in generated copies. The candidate changes clearCachedHeight in a generated copy only, keeping same-owner async observations and dropping computed/seed caches.

Cases: initial sizing control, unchanged-content clear/invalidation, later real height-change recovery control, same-size reconfiguration, seeded-cache invalidation. No synchronous sizing calls are introduced by the driver. Candidate results may disprove the hypothesis; false hypothesis_match is valid evidence. Missing controls, malformed/nonfinite/duplicate/misidentified reports or launch/compile failure are INVALID, never a successful bug reproduction.

## Parent preflight corrections

- The delivered nested snapshot struct mistakenly referenced enclosingwindow/superview instead of its capturedhasWindow/inCollection fields; fixed in generator.
- DriverhostFrame initially pointed to the outer legacy container, which would hide the very mismatch under test. It now records both outer container and real hosted view converted to collection coordinates.
- Validator now enforces exactly ordered cases, exactly rows0/1, finite numeric snapshots, live ownership flags and recomputes control/hypothesis results from raw frames. Boolean summaries alone cannot make a test green.
- The independent geometry review's blanket assumption that init'scontentView.clipsToBounds implies current legacy clipping is not proven: UIKit may replace the original contentView when applying a configuration. Capture did not record clipping. The cautious 'frame mismatch, not pixel proof' boundary remains, without claiming it must be clipped.

Local extraction/validator19 tests, Python/Bash/YAML and source-equality gates precede the native run. Only script/workflow/plan files enter this diagnostic commit. All existing dirty documents are preserved. This is not a new IPA and does not claim that the user's layout is fixed.
