# Height admission and pending-state repair

Production changes are confined to `MessageListLayout.swift`. The immutable
comparison baseline is `2f21e242df71d63576682f8ac61c50a097f3a5ae`; the delivered
baseline replay source `b47cb87` has the same production bytes.

## Native Swift policy tests

`run_tests.py` compiles the complete actual layout class, with a reversible
UIKit-import replacement and non-rendering Foundation/CoreGraphics adapters.
It also copies the real `MessageListItem` enum unchanged. No policy is
reimplemented in Python or in the platform adapters. Public admission, cache,
preparation and lifetime methods own all decisions and expected state changes.

Pinned compiler: Xcode26.2 /17C52, macOS SDK26.2, host target macOS13.0.

```sh
python3 run_tests.py --output /tmp/policy-candidate
python3 run_tests.py --output /tmp/policy-baseline \
  --source-rev b47cb87a33b4991c50396f727e39fb99387bfa60 \
  --expected-failures baseline-failures.json
python3 run_tests.py --output /tmp/policy-precalc-ablation \
  --mutation precalc-blind --expect-failure precalc_shrink_is_deferred
python3 run_tests.py --output /tmp/policy-cancellation-ablation \
  --mutation keep-pending \
  --expect-failure stable_cancels_pending_shrink \
  --expect-failure post_thaw_stable_cancels_pending_shrink \
  --expect-failure deadband_growth_cancels_pending_shrink
python3 run_tests.py --output /tmp/policy-purge-ablation \
  --mutation keep-purge-pending \
  --expect-failure width_purge_drops_new_intermediate_pending
```

A failing compile, process crash or missing result is INVALID, never an expected
regression. Immutable baseline and compiled mutants must produce the exact
recorded assertion failures. Candidate assertions must all pass. Source hashes,
compiler command and full logs are retained for every variant.

These are native **Swift policy** tests, not UIKit scheduling, SwiftUI rendering,
presentation-frame or phone visual acceptance. The adapters contain no height
admission/cache/remap policy; tests explicitly call production methods.

## Functional candidate source/build profile

- `candidate-source.json` freezes the one allowed production file and both old
  and candidate SHA256 hashes. It is not generated from the checkout at build
  time. A changed reviewed source requires an explicit manifest revision.
- `check_candidate_source.py` compares all1481 production paths to the immutable
  baseline, rejects other changes/additions, and requires the approved bytes to
  equal the pinned commit, not a dirty working tree.
- `prepare_candidate.py` keeps the original baseline overlay/profile unchanged.
  It verifies the functional profile first, then overlays only the same DEBUG
  entry/trace-read bridge. Exact inverse restoration is to the approved
  candidate; it never pretends that the layout is still baseline-identical.
- `.github/workflows/ios15-height-repair-device.yml` runs on
  `fix/ios15-height-policy-candidate`. It separately checks the unchanged
  observation-only gate in a detached immutable b47cb87 worktree, runs all policy
  variants, builds the complete real App with pinned Xcode/device SDK26.2,
  packages, and performs strict codesign verification.
- The original `.github/workflows/ios15-provisional-height-device.yml` is not
  weakened or retargeted. Separating the functional branch avoids pretending
  that a functional change can pass that observation-only source gate.

The isolated IPA is `Minis-HeightFix-<commit>.ipa`, displayed as **Minis Height
Fix**, with bundle ID `com.openminis.layoutprobe`. It replaces only the earlier
Layout Probe, not normal Minis. Normal Minis URL handlers, extensions, App
Group and Keychain access stay absent. Info.plist marks the candidate and pins
its layout SHA256. No private replay input is uploaded or bundled.

## Device acceptance remains separate

The real-list replay driver is unchanged from b47cb87. Use the existing original
150-message export, not a rewritten fixture. Capture must exercise fresh rows
while physically scrolling and include the final settled period, without a cap
or missing ownership/control evidence. `CAPTURE_VALID` only validates collection
protocol; it is not a repair verdict. Correlate low host observations, accepted
layout writes, actual frames/contentSize and settlement before judging the fix.

Do not claim that cancelling an observed obsolete value guarantees a future
asynchronous correction has already arrived at thaw. Genuine shrink and growth
must remain possible; no global monotonic-height or contentOffset workaround is
used. The original long-lived visual-gap variant is not automatically proved
fixed by the captured transient-chain repair.

## Local tooling checks

From the repository root:

```sh
python3 -m unittest discover -s scripts/ios15-height-policy -p 'test_*.py' -v
python3 scripts/ios15-height-policy/check_candidate_source.py
```

Python tests verify source profiles/overlays/report tooling, not native behavior.
