# Real-device re-entry diagnostic capture

This is a diagnostic IPA and transport/collector test, **not a layout fix** and not a simulator reproduction of the reported bug.

## Why device, not another fake renderer

The current App simulator link is blocked by device-only iSH native libraries. The already-working `generic/platform=iOS` build retains the complete production parser, text view, hosting, cell, layout and navigation. Collecting measurements on the real phone avoids replacing those components merely to get a simulator executable.

Every change in the seven UI/logging source files is inside a `REENTRY-DIAG` / `#if DEBUG` observation block. `check_source.py` removes those blocks and compares all other nonblank source lines to the immutable `1f47275` baseline. It also rejects active layout/measurement operations inside the observation blocks. This is an isolation gate, not proof that diagnostics have zero timing overhead.

## Opt-in and privacy

- Only `package_ios15_ipa.py --diagnostic-commit <full SHA>` activates recording on the **staged** app before signing.
- Ordinary packages remain off, including ordinary repacks of previously tagged diagnostic inputs. Source `.app` is never modified.
- Scalar geometry, process-local anonymous object IDs, short validated session UUIDs and a source commit identify events. No message body, URL, key, prompt or screenshot is copied into the trace.
- The recorder deduplicates identical observations, limits its fingerprint map to 1024 entries and limits each process capture to 8000 events plus an explicit truncation marker. Encoding and log/file I/O run on a serial utility queue, not in a text-size callback.
- The trace also gets its own `Library/Logs/reentry-<run UUID>.log`. Existing Settings → Logs lists it and shares the ORIGINAL file, avoiding preview truncation and unrelated daily-log content. Normal log retention applies. Relaunch begins a separate capture; do not delete the active trace while reproducing.

## Captured boundaries

- Coordinator/chat mount and chat unmount; VM ↔ collection identity.
- Cell configuration and generation; cell ↔ index identity.
- Original text intrinsic/correction measurement results; text ↔ cell identity.
- Legacy host preference, queued delivery and cell acceptance.
- Cell return source (cache, seed, legacy observation, estimate), precalc writes and layout-height corrections.
- Actual viewport/content size, explicit force emission/scroll requests, drag and settle.

The capture never sets a height, scroll mode or offset; never calls an extra `sizeThatFits`, `layoutIfNeeded`, or layout invalidation; never enables Markdown screenshots or a full-content snapshot collector. Object IDs can be reused by UIKit after teardown; ambiguous joins must remain INCONCLUSIVE.

## Gates before packaging

1. Python source-isolation negative controls and real IPA metadata fixture tests.
2. Xcode 26.2 / build 17C52 pinned native Foundation tests of the EXACT collector extracted from `AppLogger.swift`.
3. Native no-op mutation: the mutated collector must compile, then fail the expected behavioral assertion. A compiler error is INVALID, not a successful negative control.
4. DEBUG syntax parse and full iOS 15 device App type/link build with the pinned device dependencies.
5. IPA metadata, signing, checksum, source commit and diagnostics activation checks.

The Foundation tests cover disabled recording, deduplication, sequence/concurrency, budget, nonfinite values, text exclusion and dedicated file output. They do not exercise UIKit or establish that the user's visual bug is fixed.

## Device feedback loop

After receiving the verified diagnostic IPA:

1. Cover-install with TrollStore; do not uninstall or reset data.
2. Relaunch Minis. Open the existing affected conversation, leave and return, then scroll and release once.
3. Report whether the original visual jump occurred and share the newest `reentry-*.log` from Settings → Logs. No new chat export is needed.
4. Agent runs:

```sh
bash scripts/reentry-diagnostics/hardware_loop.sh <source-commit-SHA> /absolute/returned.log yes /absolute/report.json
```

The final `yes` is the user's visual observation, not inferred from an arbitrary height change. The analyzer only returns CAPTURED, INCONCLUSIVE or INVALID. CAPTURED means the required telemetry can be correlated, **never** that a fix passed. Root-cause attribution and any later behavior change require the returned real-device evidence.
