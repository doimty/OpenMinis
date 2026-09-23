# Full-source iOS 15 device baseline probe

This is a separate test App, **not a production fix**. It uses the complete
production Markdown parser/renderer, SelectableMarkdownTextView (including its
layout/correction lifecycle), LegacyHostingContentView, SelfSizingCell and
MessageListLayout. Nothing substitutes a shortened text-view class.

The rejected standalone Simulator draft is archived outside the repository at
workspace `reports/openminis-ios15/retired-sim-harness/`; do not restore its
settled-snapshot verdict or partial source extraction.

## Current entry: private real-list replay

The current App opens a local JSON picker, not the six-case quick driver. Import
one original clean session export (or its messages array), at most2MB/500 raw rows.
The file is read locally and never included in the source/IPA. Actual RawMessage
conversion, consecutive-assistant folding, completed caches and V3 Coordinator
own the displayed list. Missing exported media/tool results are recorded as limits.

After browsing to a useful position and releasing your finger, tap **开始采集**,
then **重入**, and physically flick up/down over the long replies. Release your
finger before **结束分享**, within20seconds. Reentry ensures actual Coordinator
configuration and fresh text measurements occur inside the capture window; a
warm-list scroll without those witnesses is INCOMPLETE, not a reproduction.
One window per launch; idle/warm-up is paused and the unchanged8000-event cap
remains. No fake gesture flags, scalar height writes or per-frame FPS overlays.

Use `python3 replay_report.py report.json --commit <40hex> --input-sha <64hex>`.
CAPTURE_VALID means a usable physical-motion trace, **not** bug reproduction or a
fix. INCOMPLETE and INVALID stay distinct. The old `device_report.py` and six-case
code remain for historical reports; both actual fa407d1/5d84897 reports are still
INCONCLUSIVE, and this new format must not be run through their validator.

## Build and isolation

Workflow: `.github/workflows/ios15-provisional-height-device.yml`.

1. Pin Xcode26.2 / 17C52 / device SDK26.2. Run the original observation-only
   source-isolation and native collector checks BEFORE applying a test overlay.
2. Verify `prepare_device_probe.py` and report-validation tests.
3. Reuse the established full App device dependency path; do not try to link
   device-only dependencies into a Simulator target.
4. Build from a disposable source copy. Only the application entry, the existing
   Debug file and a collector UI-window/read bridge are overlaid. Exact inverse
   restoration to immutable `2f21e24` bytes is mandatory. Every complete
   rendering/measurement source remains byte-identical.
5. Package a separate `com.openminis.layoutprobe` App named **Minis Layout Probe**.
   Remove normal Minis URL/document registration and embedded extensions. Use
   its own application identifier and no normal Minis app-group/keychain access.
   The source bundle is not changed.

Build success means compilation/package verification only. The device tests
are **NOT_RUN** until the separate App is actually launched on iOS15.

## Historical six-case device runs (fa407d1 / 5d84897)

Those earlier test Apps automatically ran neutral short-code, long-code and
plain-text fixtures using the real `SelectableMarkdownView`. The code remains
for historical reports, but is not the current replay App entry. SHA256 includes
the full raw Markdown/code content, not attachment-replacement characters.

- An independent production-rendered text view supplies a finite-width reference.
  Only the reference is explicitly measured.
- Subject snapshots only read frames/storage/visibility. They never query
  intrinsicContentSize or call sizeThatFits/layout while observing.
- Actual production diagnostic events record short-lived host/cell/cache writes.
  A final recovered snapshot cannot erase a 14ms transient from the verdict.
- Deferred policy and remove/reinsert actions are test-driven, not real gestures
  or complete normal-App navigation. No original/private conversation is copied.
- The first actual-device run completed all controls, but ordinary remove/reinsert
  kept the same measured text view and produced no unset-width event in the target
  phase. That report remains INCONCLUSIVE. The target phase now uses a fresh UIKit
  reuse identifier, still with the same production cell class/content/width; this
  creates an actually unmeasured cell/host/text tree. The report includes cell/text
  identities and requires them to differ from the initial tree. Subsequent reuse
  control keeps the regular same-pool behavior. No subject height is fabricated.
- After completion, use **分享测试报告** to share the nonce-scoped JSON. The normal
  Minis App is separate and unaffected. Restart the test App for a fresh run.

The report folder is `Documents/provisional-height-probe/<actual trace run UUID>/`.

## Local checks

```bash
python3 -m unittest discover -s scripts/ios15-provisional-height-probe -p 'test_*.py' -v
python3 -m unittest discover -s scripts/reentry-diagnostics -p 'test_*.py' -v

# macOS with the pinned Xcode only, not runnable on a Linux host:
bash scripts/reentry-diagnostics/run_collector_tests.sh /tmp/minis-collector
bash scripts/ios15-provisional-height-probe/run_capture_window_tests.sh /tmp/minis-collector
```

Python checks validate tooling/report algorithms, not native runtime acceptance.
Do not invoke sibling-import unittest modules as repository-root module names;
use discovery as above or execute each test script directly. The window runner
requires Recorder.swift produced by the first macOS runner. It compiles the
actual collector and extracted diagnostic marker callbacks (only pan state is
substituted), then separately requires compiling no-op-pause and missing-ordinal
mutations to fail assertions. These Foundation checks are not UIKit gestures.

The replay validator checks owner/VM links, real configure/text/viewport events,
boundaries, ordinal/count consistency, visible rows, and unchanged viewport width
and hashed model/text. None of these checks asserts a layout repair.

For a **historical six-case JSON only**:

```bash
python3 scripts/ios15-provisional-height-probe/device_report.py report.json --commit <probe-build-SHA>
```

- `BASELINE_REPRODUCED` / exit0: complete source/config/text→preference→delivery→
  cell acceptance→row return→cache shrink/recovery association, intact controls.
  It is a local native baseline result, not a fixed-App verdict.
- `INCONCLUSIVE` / exit1: complete valid collection but no complete target witness.
- `INVALID` / exit2: stale provenance, missing/capped trace, nonfinite/missing data,
  hidden/empty content, unavailable legacy path, or failed controls.

A future functional candidate needs the same native loop and independent
controls. Source-string tests, arbitrary revision labels, `build-for-testing`
and merely creating an IPA do not prove the original issue fixed.
