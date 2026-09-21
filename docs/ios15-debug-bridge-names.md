# Debug offload bridge names: restore on-device inspection

## Baseline and actual failing loop

Baseline source3773812539d0090d2ea4c7b175d4d2a1df3bbee9. New branch`fix/ios15-debug-bridge-names`; pre-existing dirty docs are preserved and excluded from the implementation commit.

The user ran the delivered collector unmodified (script SHA a7d8340b…): run e6ed749e-ab36-4d58-80d8-e5cdf4fc3511 returned `preflight_failed / debug_dispatch_bridge_unavailable`. The later ec7ed67e log (SHA1db73153a01e64a8939cbe384d33e88f3f1ec6fa180f94cd2e83d0a7a01d2568) confirms real execution at11:27:46–47: Python starts, `/usr/local/bin/minis-debug` is dispatched as a native builtin, exits1, and the collector writes its failure report and exits. No layout collection started. The shell's512 is its wait-status representation of the collector's nonzero exit, not evidence of a separate512-valued Python error.

The failure category comes from the DEBUG C dispatch helper when `NSClassFromString(@"DebugLocalDispatch")` is nil. The Release-only rejection and missing-selector branches have different error categories. Do not blame user operation, disable authentication, or repeatedly run the same failing collector.

## Ranked hypotheses / predictions

1. **Swift/ObjC runtime-name mismatch.** Current Swift declarations use bare`@objc`, while C looks up unqualified strings. The supplied IPA class-list contains Swift-mangled runtime names. Prediction: native cold unqualified lookups fail in the baseline, typed/qualified controls succeed, and explicit stable ObjC names make the same C dispatcher work.
2. **Bridge missing from target/link.** Prediction: typed/qualified controls or source/binary membership would fail too. Existing artifact inspection already weakens this explanation; the native controls must exclude it.
3. **Selector/shared-singleton mismatch.** Prediction: class lookup succeeds but a later selector/getter fails. The current device error happens earlier; native tests still cover these later steps so the repair is not another half-fix.

## Narrow production change

Declare stable ObjC runtime names on DebugLocalDispatch and MinisDebugLogReader, matching the two existing strings in DebugOffload.m. The log reader has the identical boundary defect; its Release availability must remain unchanged. Do not add per-module fallback strings, change selectors, alter dispatcher threading, touch debug authentication or widen UI layout scope.

## Native evidence plan

Compile the **actual production Swift bridge and log-reader sources**, plus the unmodified `dispatch_local_rpc` C function extracted from DebugOffload.m. Only downstream DebugJSONRPC/DebugViewInspector/LoggingManager are explicit test doubles; they avoid network, files and UI work unrelated to class lookup. The C lookup, sharedInstance and dispatch selectors, background/main-thread guard and bridge code itself are real.

Run baseline and candidate with the same probe driver on pinned Xcode26.2/17C52 and iOS26.2 simulator, compiled as module`Minis`, deployment15.0. Record cold lookups before typed realization, the real C background dispatch result, warm typed identity checks, selector checks and main-thread rejection. Baseline must be a **valid red** at the expected lookup boundary, not a compiler/fixture failure. Candidate must pass the unchanged checks.

Success: both named classes resolve to their actual types and C dispatch reaches the fixture RPC unchanged from a background worker; the main-thread deadlock guard still rejects. Independent failure signals: cold/warm mismatch hidden, missing typed controls, wrong selectors, request modified, stale report/nonce, baseline failure for another reason, compile failure called a regression signal, or any authentication/layout change.

After native red/green and review: run full app build/package gates, verify the new IPA and plain runtime class names in its actual binary, then deliver an inspection-entry repair. This does **not** itself claim the thinking-footer occlusion is fixed. The existing collector can then be retried once on the updated app to obtain real geometry.

## Native result and review hardening

Run35561610987 on83efa7e completed successfully. Both baseline unqualified lookups were false even after typed realization, both qualified/selector controls passed, and C dispatch returned the observed missing-bridge error. Both candidate lookups resolved to the correct types and real C background dispatch reached RPC; the main-thread rejection remained intact. Actual source hashes match3773812 and83efa7e. Runtime names changed from`Minis.DebugLocalDispatch`/`Minis.MinisDebugLogReader` to their unqualified names.

Independent review correctly asked for stronger future evidence contracts. The validator now requires the COMPLETE two-class baseline red and exact qualified names, not just the first failing dispatcher lookup. The actual first native run also passes this stricter validator. Dirty local source/probe inputs now produce candidate_commit=null, with the real HEAD, dirty paths and per-input/aggregate hashes recorded separately; a clean CI checkout still records its exact commit. A temporary git-repo test covers both source and runner changes.14 local tests pass. No additional production change was made during this hardening.
