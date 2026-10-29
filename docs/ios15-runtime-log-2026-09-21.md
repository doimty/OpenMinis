# 2026-09-21 runtime log triage

## Scope and frozen baselines

The user supplied a runtime log without a caption. This checkpoint interprets its observations; it does not assume a new user symptom, reproduce an iOS bug, or authorize a guessed rendering fix.

- Input SHA256: `d3d00e9b3c04c92cd13d354a21086f0feb218b5997499b2278659d9ec506f45c`.
- Input size: 500,150 bytes / 1,889 physical lines. Timestamped interval: 2026-09-21 07:53:31.464 through 07:53:55.922, 24.458 seconds. Starts mid-command, not at app launch.
- Source checkout and freshly queried fork branch: `fix/ios15-cell-hit-region` at `ed8393eb4333989d907b6b80eb5f280b4d03fb4e`. The worktree was clean before these evidence notes.
- Fresh GitHub query: `doimty/OpenMinis` run `35518769240`, completed/success for that SHA. Earlier `35518067558` failed for `af4f0ad`; local execution/download failures must not be substituted for the final cloud build status.
- Retained IPA: 84,272,849 bytes, SHA256 `b8eace6c96a73e71278cf239f654f23d8db7cc41c7ce214269c087745fe1e76d`, matching its retained manifest. ZIP CRC check passed; bundle `com.openminis.app`, marketing version 1.13, build 1, MinimumOSVersion 15.0.
- Retained versions evidence names `ed8393e`, Xcode 26.2 / 17C52, iphoneos SDK 26.2, iSH `3f6384c`. Nonempty 2,276,655-byte app build log has one BUILD SUCCEEDED marker and zero parsed compiler errors. No incompatible-arm64e or newer-iOS-link match in the checked log.
- **Installed-build attribution remains open:** the device excerpt has no unique build SHA/UUID. Do not equate the available source/IPA with the installed binary merely because its filename is Minis.

## Most useful geometry observation

Physical log lines refer to the private input above. Only diagnostic numbers are recorded here, not request bodies or conversation contents.

| Lines | Time | Observation |
| --- | --- | --- |
| 350 | 07:53:38.980 | Settle begins, mode=auto, contentSize=3954, offset=3341.3, one deferred height, no pending snapshot |
| 351 | 07:53:38.980 | Deferred flush reports index25 inside the viewport, height1161→36, delta−1125 |
| 360 | 07:53:39.032 | Anchor index25 frameY remains2697, offset3341→2216, reported screen-relative shift+1125 |
| 362 | 07:53:39.047 | Settle ends, contentSize2829, offset2216.3 |

This is direct evidence of a large layout/scroll-coordinate change in one settle sequence. It is **not yet proof that36 is the wrong height**, that the deferred value was stale, that a particular thumbnail/retry control was affected, or that the user saw exactly this change. There is no recording showing whether content was intentionally collapsed, nor a complete item-identity/measurement-generation trace.

Matching source semantics at the pinned SHA:

- `src/ios/Agent/MessageList/MessageListLayout.swift:318–373`, `applyDeferredHeights()`, applies the deferred preferred height to the real cache and logs its delta.
- `src/ios/Agent/MessageList/CollectionViewMessageListV3.swift:5158–5261`, `settleAfterInteraction`, consumes pending markdown corrections, flushes deferred heights, invalidates layout, and compares the same anchor index's pre/post screen coordinates. Numeric offset restoration is conditional on browsing mode; this captured settle was auto mode.

Other geometry: two FIRST-MEASURE CORRECTION records (lines68/89);12 WIDTH-GUARD records. WIDTH-GUARD returns before measuring at an invalid width (`SelectableMarkdownView.swift:7030–7067`); it is not a claim of persistent visible overflow. No `Ignoring bogus layer size` observation occurs in this excerpt. Input-bar zero at line18 is immediately followed by seed115.357 at19 and settled115.667 at93; mixing these timestamps to invent a persistent inset deficit is invalid.

## Shell and API progress are confirmed

- Lines1/2/989 report stdout/stderr inactivity while pid45 is running. These readers report lack of output, not lack of underlying work.
- Lines1807–1811: stdout/stderr EOF, pid45 exits0 after207.05s, stdout1107 characters/stderr0; coordinator completion and tool success follow. The observed command was the package-install operation, and its result reports successful installation.
- Line1835: req15 dispatched at07:53:50.196. Line1885: stream opens at07:53:52.425 (logged elapsed2.22s). Line1888: a reasoning item arrives at07:53:53.288.

The operation was not permanently stuck at the end of this excerpt. This does **not** deny an earlier long apparent wait, nor prove the subsequent model response completed: the trace ends during reasoning.

## Do not confuse warning kinds

-34 explicit WARN records:13 TextContainerGuard log records and21 ScrollDecel records. No explicit ERROR record.
-14 SLOW-FRAME records measure **gaps between scrollViewDidScroll callbacks**,32–59ms. They are not direct display/render-frame timings. Source explicitly documents this false-positive risk near the end of deceleration (`CollectionViewMessageListV3.swift:4972–4982`).
-7 separate CADisplayLink DROP records measure timestamp gaps16.7–33.3ms versus expected8.3ms. These support intermittent missed refresh intervals, not a continuously59ms render time. The three completed-phase summaries log about115.5–117.9 callbacks/s; these are not app-wide displayed-FPS measurements.
-TextContainerGuard logged13 sampled warning records, not necessarily13 invocations. Its shared cumulative counter is4033 at the first emitted sample and4241 at the last. Do not call4241 this window's count, or claim an exact window total from sampled cumulative values. Invalid0×−8 inputs are rejected;10,000,000pt **height** is the intentionally bounded unbounded-container convention, not automatically a10M-wide drawn layer (`NSTextContainerSetSizeGuard.m:53–126`).
-1,544 SessionFileTracker info records, reporting2,392 ignored event entries, peak529 records within one wall-clock second. They are filtered filesystem activity outside session subdirectories while packages are installed. Source logs that branch at `SessionFileChangeTracker.swift:89–93`. This is a logging-volume finding, **not a proven CPU/scroll-stall cause**, and not evidence the package writes failed.
-6 memory status samples show pressure=warn, app footprint113.2–231.7MiB in the captured samples, later dropping after command completion. These delta-triggered samples are not a continuous peak measurement or proof of a leak/OOM.

## Evidence tooling and independent cross-check

Workspace evidence directory: `reports/openminis-ios15/log-2026-09-21-d3d00e9/`.

- `summarize_log.py` parses the entire input and emits allowlisted diagnostic events, line numbers, counts, input SHA, and separate scroll-callback/display-link/settle metrics. It excludes request-body continuations and conversation-bearing categories.
- `python3 summarize_log.py --self-test`:5 tests pass, including inactivity→success, body non-export, cumulative counters, tracker record/event distinction, and callback/display-link separation.
- `python3 summarize_log.py <private-input> --events`: deterministic observation summary in `summary.json`.
- These are **parser tests, not reproduction of the production bug**. A log remaining abnormal after parsing cannot show a production patch works.
- One existing independent reviewer was reused after the fresh subagent invocation was rejected. Its report agrees on key counts, pid45/req15 progress, and the1125pt shift. Main-review calibration: its wording “slow frames” must be read as scroll callback gaps, its13 guard count as13 emitted samples, and the two user-bubble estimates as two different estimates rather than one bubble changing0→70 attachment height. The distinctions in this document are authoritative.

## Feedback-loop gate / resume

No production code, commit, push, new cloud build, or new IPA in this turn. Only evidence tooling and documentation were added.

The original user-visible symptom for this attachment is unspecified. The Linux host has no Swift/Xcode/iOS runtime. No actual production-path red/green reproduction has been run for this settle event. This is an explicit diagnostic stopping boundary, not a claim of repair.

Before any fix: correlate the user's observed symptom and screenshot/recording timing with this event; if it is the jump, capture stable item identity, deferred measurement generation, old/new preferred heights, and the actual rendered content at flush. Reproduce with the real collection/cell/legacy-host path. Do not replace the real hierarchy with hand-positioned coordinates, guess another inset constant, or use a passing structural/packaging test as device acceptance.
