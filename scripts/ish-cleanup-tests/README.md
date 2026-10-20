# iSH cleanup boundary characterization

## Current production ticket regression

The new production entry points are checked with:

```sh
python3 scripts/test_ish_execution_identity.py --out /tmp/ish-ticket-NEW
python3 scripts/verify_ish_ticket_integration.py --out /tmp/ish-static-NEW
bash scripts/test_ish_command_lifecycle.sh
```

The first command compiles the real identity header, plus exact extracted token
signal and cancelled-start helper bodies. It runs 28 assertions (13 allocator,
10 signal scan, 5 cancelled-start) and three separately compiled runtime mutants.
The host task table and task_start are fake; no real process signal is sent.
The second command is STATIC only, not an Apple compiler. The third compiles
and runs the actual Swift lifecycle on macOS; Linux returns 77 (SKIP). A green
C fixture/static check alone does not release the production executor.

The previous baseline-only script below deliberately rejects the new Swift
source layout (exit 2), not claims a repaired executor passed its old model.
Historical a45009b results remain in the separate evidence directory. The old
killProcessGroup body is retained for API compatibility, but production timeout
and Stop no longer call it. Do not use the old characterization as a new gate.

## Historical baseline characterization

These fixtures **demonstrate baseline defects**. They are not a completed fix,
not native Swift tests, and not a real iSH/kernel/device recovery test.

## Run on the current Linux analysis host

Requirements: Python 3, `tree_sitter`, `tree_sitter_swift`, clang with Blocks
support, pthreads, and `libBlocksRuntime` (present on the analysis host). This
fixture uses `pthread_timedjoin_np`; it is not a macOS/XCTest runner.

Use a new output directory for every run (existing output is rejected):

```sh
python3 scripts/test_ish_cleanup_boundaries.py --characterize --out /tmp/ish-characterize-NEW
python3 scripts/test_ish_cleanup_boundaries.py --gate --out /tmp/ish-gate-NEW
```

Exit codes:
- `--characterize`: 0 means the expected baseline observations and runtime
  mutation checks were obtained. **It does not mean the executor is fixed.**
- `--gate`: 1 while the known baseline risks remain; this is the current result.
- 2 means unsupported source layout, missing dependency, compilation failure,
  invalid output, missed mutation, or fixture failure. It is never a pass.

The output contains JSON evidence, exact production slices, byte/line ranges
and SHA-256, generated host code, compile logs and runtime logs. Swift source
layout is recognized conservatively; an implementation change requires review
of the extraction/replay, not blindly changing expected strings to get green.

## What is actually executed

The production `ISHTaskIsDescendantOf` and `killProcessGroup` bodies are copied
byte-for-byte from `src/ios/iSH/ISHShellExecutor.m`. Only the Objective-C method
signature is changed into a C function signature; the driver asserts the body
is unchanged. The fixture is compiled as Objective-C with Blocks support.

Kernel structs/pid lookup/signal delivery/dispatch and logging are substitutes.
No `kill(2)` is called. Fake signals only increment arrays, the one delayed
block is held and explicitly invoked, and the lock contention test holds only
a fixture-local pthread mutex, releases it, then bounded-joins its worker.
An 8-second alarm and 10-second parent subprocess timeout cap a broken fixture.

Ten observations cover seven safety cases, two conditional baseline hazards,
and one actual host mutex-blocking case. Four separately compiled runtime
mutants remove deferred identity checking, remove ancestry matching, allow
PID 1, and remove stale-parent identity checking. A mutant is caught only when it **compiles** and runtime assertions fail;
a compiler error is a fixture error, not a caught mutant.

## What is only AST/dependency evidence

The driver extracts the real timeout body, queue declaration/scheduling and all
three Stop paths with tree-sitter. It verifies the shared serial queue and the
order claim -> kill -> finalize -> clear PID -> resume, then replays those
specific dependencies with a blocked kill.

This does not run GCD/Swift continuations, full `runCommand`, the ObjC registry,
reader lifetimes, startup, callbacks, task identity or the real kernel. It does
not prove iOS compatibility, PID-reuse occurrence rates, a fix, or the cause of
a user's observed SSH hang. A true Swift/ObjC native regression and real-device
validation are still required before production changes can be released.
