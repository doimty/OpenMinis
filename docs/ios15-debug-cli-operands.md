# Debug CLI first-operand repair

Baseline922927a2b2fbfb19e7b26155238a67eb0e28af83; new branchfix/ios15-debug-cli-operands.

## New device evidence

Log8a28f4fb/SHA894a49c3f13159c1ef64627e1a4040f41c144d743ba7f905b963a620ce2898ef covers10:12–14:53. It records the eda57145 collector foreground native call exiting0, then three consecutive background minis-debug calls at14:52:37 with exit0,0,2, followed immediately by the failed report. This matches the immutable a7 script sequence discover→viewTree→inspect. It narrows the failure away from the earlier generic serialization guess: inspect exits invalid-arguments before RPC.

`noff_positional_args` explicitly removes the subcommand and returns operands. `DebugOffload.second_positional` wrongly treats that array as including the subcommand, requires count>=2 and returnspos[1]. A documented one-address inspect therefore getsnil, emits 'inspect requires a view address' and exits2. With two distinct operands it selects the wrong one.

## Feedback loop and scope

`scripts/test_debug_cli_operands.py` extracts the real common parser, operand selector and cmd_inspect. Only RPC/output sinks are test doubles. On GNUstep the one array-subscript expression is spelled as objectAtIndex with the SAME index; Apple compiles original syntax. Canonical single-address calls fail in both immutable baseline and unmodified production source, while a missing-address control fails as intended and duplicate-identical-address control succeeds. Compilation failure is invalid evidence, not red.

Narrow production repair: return the first operand and correct the misleading comment. Do not change the shared parser, authentication, UI, layouts or special exec joining. Sibling debug commands using this accessor will now honor their documented first operand. Their IO behavior is not exercised by the inspect-only test.

Success: canonical inspect reaches the RPC sink with exactly the supplied first address. Independent failures: missing operand accepted, wrong target forwarded, test sink used instead of the real parser, compile failure counted as repro, or source drift. The native test checks5 vectors in both versions. Original name-bridge tests and full build/package gates still apply.

## Temporary read-only capture compatibility

Until the fixed App is installed, a collector may retry ONLY an inspect rejection that explicitly reports missing address/invalid_args/exit2. Duplicate the SAME validated hex address, not a different token. The native control proves this reads the same intended address on old and corrected selectors. Latch that compatibility mode for the remainder of a run to avoid repeating the failed canonical request. Report that compatibility was used. Do not use it for unknown errors, invalid addresses, authorization, other commands or a returned serialization error. Retire the workaround once the user is on the fixed App; canonical calls remain the default and succeed without it on fixed code.

This repairs diagnostic CLI argument handling, not the user's visual layout bug. The log also contains an InputBarHealth no-callback watchdog warning; retain it as a separate heuristic signal, not proof that the generic capture failure or screenshot was caused by that warning.
