# Full-source iOS 15 device baseline probe

This is a separate test App, **not a production fix**. It uses the complete
production Markdown parser/renderer, SelectableMarkdownTextView (including its
layout/correction lifecycle), LegacyHostingContentView, SelfSizingCell and
MessageListLayout. Nothing substitutes a shortened text-view class.

The rejected standalone Simulator draft is archived outside the repository at
workspace `reports/openminis-ios15/retired-sim-harness/`; do not restore its
settled-snapshot verdict or partial source extraction.

## Build and isolation

Workflow: `.github/workflows/ios15-provisional-height-device.yml`.

1. Pin Xcode26.2 / 17C52 / device SDK26.2. Run the original observation-only
   source-isolation and native collector checks BEFORE applying a test overlay.
2. Verify `prepare_device_probe.py` and report-validation tests.
3. Reuse the established full App device dependency path; do not try to link
   device-only dependencies into a Simulator target.
4. Build from a disposable source copy. Only the application entry, the existing
   Debug file and a collector read-only extension are overlaid. Exact inverse
   restoration to immutable `2f21e24` bytes is mandatory. Every complete
   rendering/measurement source remains byte-identical.
5. Package a separate `com.openminis.layoutprobe` App named **Minis Layout Probe**.
   Remove normal Minis URL/document registration and embedded extensions. Use
   its own application identifier and no normal Minis app-group/keychain access.
   The source bundle is not changed.

Build success means compilation/package verification only. The device tests
are **NOT_RUN** until the separate App is actually launched on iOS15.

## Device run

The test App automatically runs neutral short-code, long-code and plain-text
fixtures using the real `SelectableMarkdownView`. SHA256 includes the full raw
Markdown/code content, not the attachment-replacement characters.

- An independent production-rendered text view supplies a finite-width reference.
  Only the reference is explicitly measured.
- Subject snapshots only read frames/storage/visibility. They never query
  intrinsicContentSize or call sizeThatFits/layout while observing.
- Actual production diagnostic events record short-lived host/cell/cache writes.
  A final recovered snapshot cannot erase a 14ms transient from the verdict.
- Deferred policy and remove/reinsert actions are test-driven, not real gestures
  or complete normal-App navigation. No original/private conversation is copied.
- After completion, use **分享测试报告** to share the nonce-scoped JSON. The normal
  Minis App is separate and unaffected. Restart the test App for a fresh run.

The report folder is `Documents/provisional-height-probe/<actual trace run UUID>/`.

## Local checks

```bash
python3 scripts/ios15-provisional-height-probe/test_prepare_device_probe.py -v
python3 scripts/ios15-provisional-height-probe/test_device_report.py -v
python3 scripts/reentry-diagnostics/test_isolation.py -v
```

These are tooling/report-algorithm checks, not native runtime acceptance.

After receiving the JSON:

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
