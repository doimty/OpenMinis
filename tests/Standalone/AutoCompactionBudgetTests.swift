// AutoCompactionBudgetTests.swift
//
// [T-auto-compact-budget] Standalone regression for the automatic compaction
// soft budget. This file does NOT embed a copy of the production types: the
// runner scripts/test_auto_compaction_budget.py extracts
// `import Foundation` + `AutoCompactionPreferences` + `ContextPolicy` VERBATIM
// (byte-preserving) from src/ios/Agent/Chat/ContextPolicy.swift — everything
// before `// MARK: - Outbound context measurement` — and compiles it together
// with this file:
//
//   swiftc -parse-as-library <extracted>.swift AutoCompactionBudgetTests.swift -o runner
//   ./runner                 # exit 0 = all executed checks pass, 1 = FAIL
//
// Rationale: ContextSizeMeter below that marker depends on AgentMessage and
// the provider model layer, so the extractable unit is exactly the pure types
// under test (preferences + policy math), still using the real production
// bytes rather than a hand-copied duplicate.
//
// Without a Swift toolchain the python runner reports the native run as
// SKIP (exit 77) and never as a pass: tree-sitter source checks are NOT
// execution evidence.
//
// Output contract (one line per assertion):
//   PASS[<check-id>] / FAIL[<check-id>] <detail> / SKIP[<check-id>] <why>
// followed by `SUMMARY pass=<n> fail=<n> skip=<n>`.
// Check ids are stable: the python mutation harness matches FAIL[<id>] so a
// mutant can only be counted as rejected when the SEMANTIC assertion fails
// (never on a compile/syntax blow-up).
//
// Oracle note: every expected literal below is derived from the plan spec
// (Auto-compact is a SOFT target: compact at 85%, offload at 70%, settle to
// 55%; legacy tiers untouched; budget only lowers, never raises, the policy
// line) and from the acceptance criteria — not by copying production source.
// The `// @case` lines are re-derived independently by the python script with
// exact rational arithmetic; a mismatch there fails as well.
//
// UserDefaults isolation: suite-based checks use a fresh per-run suite name.
// Checks that must write UserDefaults.standard run only when the runner sets
// ACB_ALLOW_STANDARD_DEFAULTS=1 (the runner does that only with an isolated
// HOME or an explicit --allow-standard-defaults); otherwise they are
// reported as SKIP. The runner snapshots and restores the two keys anyway.
import Foundation
#if canImport(Glibc)
import Glibc
#endif

// MARK: - Harness

private final class BudgetHarness {
    private(set) var pass = 0
    private(set) var fail = 0
    private(set) var skip = 0

    func expect(_ id: String, _ condition: Bool, _ detail: @autoclosure () -> String = "") {
        if condition {
            pass += 1
            print("PASS[\(id)]")
        } else {
            fail += 1
            let d = detail()
            print("FAIL[\(id)]\(d.isEmpty ? "" : " " + d)")
        }
    }

    func expectEq(_ id: String, _ actual: Int, _ expected: Int, _ label: String = "") {
        if actual == expected {
            pass += 1
            print("PASS[\(id)]\(label.isEmpty ? "" : " " + label)")
        } else {
            fail += 1
            print("FAIL[\(id)] \(label.isEmpty ? "" : label + ": ")expected \(expected), actual \(actual)")
        }
    }

    func expectEq(_ id: String, _ actual: String, _ expected: String, _ label: String = "") {
        if actual == expected {
            pass += 1
            print("PASS[\(id)]\(label.isEmpty ? "" : " " + label)")
        } else {
            fail += 1
            print("FAIL[\(id)] \(label.isEmpty ? "" : label + ": ")expected \(expected), actual \(actual)")
        }
    }

    func skipCheck(_ id: String, _ why: String) {
        skip += 1
        print("SKIP[\(id)] \(why)")
    }
}

private func resultName(_ r: ContextPolicy.CheckResult) -> String {
    switch r {
    case .ok: return "ok"
    case .needsCompact: return "needsCompact"
    case .exhausted: return "exhausted"
    }
}

private func stepName(_ s: ContextPolicy.InLoopStep) -> String {
    switch s {
    case .proceed: return "proceed"
    case .compact: return "compact"
    case .sendWithinWindow: return "sendWithinWindow"
    case .sendUncalibratedOnce: return "sendUncalibratedOnce"
    case .stop: return "stop"
    }
}

private func freshSuiteName() -> String {
    return "AutoCompactionBudgetTests." + UUID().uuidString
}

private func suiteDefaults(_ name: String) -> UserDefaults {
    return UserDefaults(suiteName: name) ?? UserDefaults.standard
}

private func put(_ d: UserDefaults, _ value: Any?, key: String) {
    if let value = value {
        d.set(value, forKey: key)
    } else {
        d.removeObject(forKey: key)
    }
}

private let allowStandardDefaults =
    ProcessInfo.processInfo.environment["ACB_ALLOW_STANDARD_DEFAULTS"] == "1"

// MARK: - Preferences (keys, constants, validation)

private func runPreferenceChecks(_ h: BudgetHarness) {
    // prefs.legacy_enabled_key: the legacy opt-in key must stay unchanged so
    // existing user configuration keeps working across the upgrade.
    h.expectEq("prefs.legacy_enabled_key", AutoCompactionPreferences.enabledKey,
               "autoCompactOnThreshold")

    // prefs.budget_key_stable: new key, must be distinct from the legacy key.
    h.expectEq("prefs.budget_key_stable", AutoCompactionPreferences.budgetKey,
               "autoCompactBudgetTokens")
    h.expect("prefs.budget_key_stable",
             AutoCompactionPreferences.budgetKey != AutoCompactionPreferences.enabledKey,
             "budget key must differ from enabled key")

    // prefs.defaults_constants: default 256000; selectable range 32000...4000000.
    h.expectEq("prefs.defaults_constants", AutoCompactionPreferences.defaultBudgetTokens, 256_000, "default")
    h.expectEq("prefs.defaults_constants", AutoCompactionPreferences.minimumBudgetTokens, 32_000, "min")
    h.expectEq("prefs.defaults_constants", AutoCompactionPreferences.maximumBudgetTokens, 4_000_000, "max")

    // prefs.isvalid_matrix: only 0 (follow model) and the inclusive range are
    // accepted; everything else is invalid.
    for v in [0, 32_000, 256_000, 4_000_000] as [Int] {
        h.expect("prefs.isvalid_matrix", AutoCompactionPreferences.isValidBudget(v), "\(v) must be valid")
    }
    for v in [1, 31_999, 4_000_001, -1, -32_000, Int.min, Int.max] as [Int] {
        h.expect("prefs.isvalid_matrix", !AutoCompactionPreferences.isValidBudget(v), "\(v) must be invalid")
    }

    // prefs.normalize_invalid_to_default: invalid -> 256000; valid kept as-is.
    h.expectEq("prefs.normalize_invalid_to_default", AutoCompactionPreferences.normalizedBudget(-1), 256_000)
    h.expectEq("prefs.normalize_invalid_to_default", AutoCompactionPreferences.normalizedBudget(1), 256_000)
    h.expectEq("prefs.normalize_invalid_to_default", AutoCompactionPreferences.normalizedBudget(31_999), 256_000)
    h.expectEq("prefs.normalize_invalid_to_default", AutoCompactionPreferences.normalizedBudget(4_000_001), 256_000)
    h.expectEq("prefs.normalize_invalid_to_default", AutoCompactionPreferences.normalizedBudget(0), 0)
    h.expectEq("prefs.normalize_invalid_to_default", AutoCompactionPreferences.normalizedBudget(32_000), 32_000)
    h.expectEq("prefs.normalize_invalid_to_default", AutoCompactionPreferences.normalizedBudget(4_000_000), 4_000_000)
}

// MARK: - Preferences (persisted value coercion)

private func runPersistedValueChecks(_ h: BudgetHarness) {
    let suiteName = freshSuiteName()
    let suite = suiteDefaults(suiteName)
    let key = AutoCompactionPreferences.budgetKey

    // prefs.persisted_missing_default: absent key -> 256000 (not "0 follow").
    suite.removeObject(forKey: key)
    h.expectEq("prefs.persisted_missing_default", AutoCompactionPreferences.budgetTokens(in: suite),
               256_000, "missing")
    put(suite, 128_000, key: key)
    suite.removeObject(forKey: key)
    h.expectEq("prefs.persisted_missing_default", AutoCompactionPreferences.budgetTokens(in: suite),
               256_000, "removed after a write")

    // prefs.persisted_valid_values: persisted integers inside {0} U [32000, 4000000].
    for (value, label) in [(0, "0 follow"), (32_000, "min"), (128_000, "128K"), (256_000, "default"),
                           (512_000, "512K"), (4_000_000, "max")] as [(Int, String)] {
        put(suite, value, key: key)
        h.expectEq("prefs.persisted_valid_values", AutoCompactionPreferences.budgetTokens(in: suite),
                   value, label)
    }

    // prefs.persisted_live_reread / prefs.persisted_shared_store: the same
    // store is re-read on every call (no caching in the pure type), and two
    // UserDefaults instances on the same suite name observe the same value —
    // the "all sessions read one global preference at decision time" contract
    // at store level. (Platform caveat: cross-instance coalescing is
    // Foundation's; on Darwin/CI it is guaranteed, and this id isolates a
    // hypothetical platform difference from the same-object re-read.)
    let suiteB = suiteDefaults(suiteName)
    put(suite, 128_000, key: key)
    h.expectEq("prefs.persisted_shared_store", AutoCompactionPreferences.budgetTokens(in: suiteB),
               128_000, "second instance on same suite name")
    put(suite, 512_000, key: key)
    h.expectEq("prefs.persisted_live_reread", AutoCompactionPreferences.budgetTokens(in: suite),
               512_000, "re-read after change")
    h.expectEq("prefs.persisted_live_reread", AutoCompactionPreferences.budgetTokens(in: suiteB),
               512_000, "re-read through second instance")

    // prefs.persisted_int_out_of_range: 1 / 31999 / 4000001 / negatives / overflow.
    for value in [1, 31_999, 4_000_001, -1, -32_000, Int.min, Int.max] as [Int] {
        put(suite, value, key: key)
        h.expectEq("prefs.persisted_int_out_of_range", AutoCompactionPreferences.budgetTokens(in: suite),
                   256_000, "int \(value)")
    }

    // prefs.persisted_string_fallback: a corrupt string must NOT be coerced by
    // integer(forKey:)-style lookups (a numeric string would become a budget).
    for value in ["200000", "abc", "", "0"] as [String] {
        put(suite, value, key: key)
        h.expectEq("prefs.persisted_string_fallback", AutoCompactionPreferences.budgetTokens(in: suite),
                   256_000, "string '\(value)'")
    }

    // prefs.persisted_bool_fallback: a Boolean stored under the key must fall
    // back to the default — `false` must NOT mean "follow the model window".
    put(suite, true, key: key)
    h.expectEq("prefs.persisted_bool_fallback", AutoCompactionPreferences.budgetTokens(in: suite),
               256_000, "bool true")
    put(suite, false, key: key)
    h.expectEq("prefs.persisted_bool_fallback", AutoCompactionPreferences.budgetTokens(in: suite),
               256_000, "bool false (zero-trap)")

    // prefs.persisted_fraction_fallback: non-integral doubles are invalid;
    // an integral double within range is a legitimate encoding.
    for value in [31_999.5, 32_000.5, 4_000_000.75, 0.5] as [Double] {
        put(suite, value, key: key)
        h.expectEq("prefs.persisted_fraction_fallback", AutoCompactionPreferences.budgetTokens(in: suite),
                   256_000, "double \(value)")
    }
    put(suite, 256_000.0, key: key)
    h.expectEq("prefs.persisted_fraction_fallback", AutoCompactionPreferences.budgetTokens(in: suite),
               256_000, "exact integral double")

    // prefs.persisted_nonfinite_overflow_fallback
    for (value, label) in [(Double.nan, "nan"), (Double.infinity, "inf"),
                           (-Double.infinity, "-inf"),
                           (Double.greatestFiniteMagnitude, "greatestFinite")] as [(Double, String)] {
        put(suite, value, key: key)
        h.expectEq("prefs.persisted_nonfinite_overflow_fallback",
                   AutoCompactionPreferences.budgetTokens(in: suite), 256_000, label)
    }
    put(suite, Int64.max, key: key)
    h.expectEq("prefs.persisted_nonfinite_overflow_fallback",
               AutoCompactionPreferences.budgetTokens(in: suite), 256_000, "int64 max")
}

// MARK: - Preferences (live gating: enabled x budget, standard domain)

private func runStandardPreferenceChecks(_ h: BudgetHarness) {
    guard allowStandardDefaults else {
        h.skipCheck("prefs.active_live_read",
                    "standard-domain writes disabled (set ACB_ALLOW_STANDARD_DEFAULTS=1 in an isolated HOME)")
        return
    }
    let std = UserDefaults.standard
    let enabledKey = AutoCompactionPreferences.enabledKey
    let budgetKey = AutoCompactionPreferences.budgetKey
    let savedEnabled = std.object(forKey: enabledKey)
    let savedBudget = std.object(forKey: budgetKey)
    defer {
        put(std, savedEnabled, key: enabledKey)
        put(std, savedBudget, key: budgetKey)
    }

    // Enabled == false => active budget is 0 (follow the model/group window),
    // regardless of the persisted budget.
    put(std, false, key: enabledKey)
    put(std, 200_000, key: budgetKey)
    h.expectEq("prefs.active_live_read", AutoCompactionPreferences.activeBudgetTokens, 0,
               "disabled => 0 even with persisted budget")
    h.expect("prefs.active_live_read", !AutoCompactionPreferences.enabled)

    // Enabled == true => persisted budget, read at decision time.
    put(std, true, key: enabledKey)
    h.expectEq("prefs.active_live_read", AutoCompactionPreferences.activeBudgetTokens, 200_000,
               "enabled => persisted budget")

    // Budget 0 with the switch on => still "follow the model window" (0).
    put(std, 0, key: budgetKey)
    h.expectEq("prefs.active_live_read", AutoCompactionPreferences.activeBudgetTokens, 0,
               "enabled but budget 0 => follow")

    // A change between two decisions must be visible without restarting the
    // process (cached ViewModels read the same global preference each time).
    put(std, 512_000, key: budgetKey)
    h.expectEq("prefs.active_live_read", AutoCompactionPreferences.activeBudgetTokens, 512_000,
               "re-read sees latest")
    put(std, false, key: enabledKey)
    h.expectEq("prefs.active_live_read", AutoCompactionPreferences.activeBudgetTokens, 0,
               "flip off => 0")
    put(std, true, key: enabledKey)
    h.expectEq("prefs.active_live_read", AutoCompactionPreferences.activeBudgetTokens, 512_000,
               "flip on => latest budget")

    // Bad persisted budget while enabled => default 256000, never the "0 follow"
    // special value (that would silently disable the soft target).
    put(std, "corrupt", key: budgetKey)
    h.expectEq("prefs.active_live_read", AutoCompactionPreferences.activeBudgetTokens, 256_000,
               "corrupt => default")
    put(std, 4_000_001, key: budgetKey)
    h.expectEq("prefs.active_live_read", AutoCompactionPreferences.activeBudgetTokens, 256_000,
               "out of range => default")
}

// MARK: - Policy math: the soft budget only LOWERS the line

private func runSoftBudgetPolicyChecks(_ h: BudgetHarness) {
    // The acceptance case: a 1,050,000-token model with the default 256,000
    // budget compacts at 85% (=217,600), offloads at 70% (=179,200), settles
    // back to 55% (=140,800), and keeps auto-compact available.
    do {
        let p = ContextPolicy(contextWindow: 1_050_000, autoCompactBudgetTokens: 256_000)
        h.expectEq("policy.soft_budget_1050000_256000", p.compactThreshold, 217_600, "compact 85%")
        h.expectEq("policy.soft_budget_1050000_256000", p.offloadThreshold, 179_200, "offload 70%")
        h.expectEq("policy.soft_budget_1050000_256000", p.offloadTarget, 140_800, "target 55%")
        h.expect("policy.soft_budget_1050000_256000", !p.exhaustedOnly, "soft budget keeps compaction available")
        h.expect("policy.soft_budget_1050000_256000", p.manualCompactAllowed)
        h.expect("policy.soft_budget_1050000_256000",
                 p.compactThreshold < 1_050_000 && p.offloadThreshold < p.compactThreshold,
                 "soft lines lie strictly below the real window")
    }

    // check() boundary at the soft compact line; below the line is sendable.
    do {
        let p = ContextPolicy(contextWindow: 1_050_000, autoCompactBudgetTokens: 256_000)
        h.expectEq("policy.soft_budget_check_boundaries",
                   resultName(p.check(estimatedTokens: 217_599, contextWindow: 1_050_000)), "ok", "below")
        h.expectEq("policy.soft_budget_check_boundaries",
                   resultName(p.check(estimatedTokens: 217_600, contextWindow: 1_050_000)), "needsCompact", "at line")
        h.expect("policy.soft_budget_check_boundaries", !p.shouldOffload(estimatedTokens: 179_199))
        h.expect("policy.soft_budget_check_boundaries", p.shouldOffload(estimatedTokens: 179_200))
        h.expectEq("policy.soft_budget_check_boundaries",
                   resultName(p.check(estimatedTokens: 179_200, contextWindow: 1_050_000)), "ok",
                   "offload zone alone does not force a compact")
    }

    // Ladder of selectable budgets: thresholds are exactly 85/70/55% of the
    // budget and strictly increase with it, always below the native line.
    for (budget, compact, offload, target) in [(32_000, 27_200, 22_400, 17_600),
                                               (64_000, 54_400, 44_800, 35_200),
                                               (128_000, 108_800, 89_600, 70_400),
                                               (256_000, 217_600, 179_200, 140_800),
                                               (512_000, 435_200, 358_400, 281_600)] as [(Int, Int, Int, Int)] {
        let p = ContextPolicy(contextWindow: 1_050_000, autoCompactBudgetTokens: budget)
        h.expectEq("policy.soft_budget_ladder", p.compactThreshold, compact, "budget \(budget)")
        h.expectEq("policy.soft_budget_ladder", p.offloadThreshold, offload, "budget \(budget)")
        h.expectEq("policy.soft_budget_ladder", p.offloadTarget, target, "budget \(budget)")
        h.expect("policy.soft_budget_ladder", p.compactThreshold < 1_030_000,
                 "never above the native compact line (1030000)")
    }

    // A mid-size budget on a bigger window: 1,000,000 out of 2,048,000.
    do {
        let p = ContextPolicy(contextWindow: 2_048_000, autoCompactBudgetTokens: 1_000_000)
        h.expectEq("policy.soft_budget_bigger_window", p.compactThreshold, 850_000)
        h.expectEq("policy.soft_budget_bigger_window", p.offloadThreshold, 700_000)
        h.expectEq("policy.soft_budget_bigger_window", p.offloadTarget, 550_000)
    }

    // NOT ONE STEP UP: a budget greater than or equal to the real window must
    // not expand the model window (it would be a hard-cap bypass), and a
    // budget above the GROUP cap must not expand the cap either.
    do {
        let native = (compact: 1_030_000, offload: 1_010_000, target: 990_000)
        let over = ContextPolicy(contextWindow: 1_050_000, autoCompactBudgetTokens: 4_000_000)
        h.expectEq("policy.soft_budget_never_expands", over.compactThreshold, native.compact, "budget > window")
        h.expectEq("policy.soft_budget_never_expands", over.offloadThreshold, native.offload, "budget > window")
        h.expectEq("policy.soft_budget_never_expands", over.offloadTarget, native.target, "budget > window")
        let equal = ContextPolicy(contextWindow: 1_050_000, autoCompactBudgetTokens: 1_050_000)
        h.expectEq("policy.soft_budget_never_expands", equal.compactThreshold, native.compact, "budget == window")
        h.expectEq("policy.soft_budget_never_expands", equal.offloadThreshold, native.offload, "budget == window")
        h.expectEq("policy.soft_budget_never_expands", equal.offloadTarget, native.target, "budget == window")
        // One token below the window is a legitimate (maximal) soft target:
        // it must lower the line, never raise it.
        let just = ContextPolicy(contextWindow: 1_050_000, autoCompactBudgetTokens: 1_049_999)
        h.expect("policy.soft_budget_never_expands",
                 just.compactThreshold > 0 && just.compactThreshold < native.compact,
                 "just below window still only lowers")
        h.expect("policy.soft_budget_never_expands",
                 just.offloadThreshold < native.offload && just.offloadTarget < native.target)
        // User cap on a big model: a large budget must not push the cap's 85%
        // line above the cap itself.
        let cap = ContextPolicy(contextWindow: 200_000, isUserCap: true, autoCompactBudgetTokens: 4_000_000)
        h.expectEq("policy.soft_budget_never_expands", cap.compactThreshold, 170_000, "cap 85%")
        h.expectEq("policy.soft_budget_never_expands", cap.offloadThreshold, 140_000, "cap 70%")
        h.expectEq("policy.soft_budget_never_expands", cap.offloadTarget, 110_000, "cap 55%")
    }

    // The soft budget on a user-capped window behaves exactly like a smaller
    // soft target (proportional to the budget, which lies below the cap).
    do {
        let p = ContextPolicy(contextWindow: 1_050_000, isUserCap: true, autoCompactBudgetTokens: 256_000)
        h.expectEq("policy.soft_budget_user_cap", p.compactThreshold, 217_600)
        h.expectEq("policy.soft_budget_user_cap", p.offloadThreshold, 179_200)
        h.expectEq("policy.soft_budget_user_cap", p.offloadTarget, 140_800)
        h.expect("policy.soft_budget_user_cap", !p.exhaustedOnly && p.manualCompactAllowed)
    }
}

// MARK: - Policy math: legacy behaviour with the budget off (0 / absent)

private func runLegacyTierChecks(_ h: BudgetHarness) {
    // Each tier is checked with the budget parameter absent/default AND with an
    // explicit 0 (follow the model window): both must be the untouched legacy
    // table, including the 85%-of-group-cap behaviour for a user cap.
    let tiers: [(window: Int, offload: Int, target: Int, compact: Int, exhaustedOnly: Bool, manual: Bool)] = [
        (16_000, 0, 0, 0, true, false),
        (31_999, 0, 0, 0, true, false),
        (32_000, 22_000, 17_000, 0, true, true),
        (63_999, 53_999, 48_999, 0, true, true),
        (64_000, 44_000, 34_000, 54_000, false, true),
        (127_999, 107_999, 97_999, 117_999, false, true),
        (128_000, 88_000, 68_000, 108_000, false, true),
        (1_050_000, 1_010_000, 990_000, 1_030_000, false, true),
    ]
    for t in tiers {
        let p = ContextPolicy(contextWindow: t.window, isUserCap: false, autoCompactBudgetTokens: 0)
        h.expectEq("policy.legacy_tiers_budget0", p.offloadThreshold, t.offload, "w=\(t.window)")
        h.expectEq("policy.legacy_tiers_budget0", p.offloadTarget, t.target, "w=\(t.window)")
        h.expectEq("policy.legacy_tiers_budget0", p.compactThreshold, t.compact, "w=\(t.window)")
        h.expectEq("policy.legacy_tiers_budget0", String(p.exhaustedOnly), String(t.exhaustedOnly), "w=\(t.window)")
        h.expectEq("policy.legacy_tiers_budget0", String(p.manualCompactAllowed), String(t.manual), "w=\(t.window)")
    }

    // Explicit budget values that are INVALID (outside {0} U [32000, 4000000])
    // must not produce a soft policy at all.
    for bad in [-1, 1, 31_999, 4_000_001] as [Int] {
        let p = ContextPolicy(contextWindow: 1_050_000, autoCompactBudgetTokens: bad)
        h.expectEq("policy.invalid_budget_keeps_native", p.compactThreshold, 1_030_000, "budget \(bad)")
        h.expectEq("policy.invalid_budget_keeps_native", p.offloadThreshold, 1_010_000, "budget \(bad)")
        h.expectEq("policy.invalid_budget_keeps_native", p.offloadTarget, 990_000, "budget \(bad)")
    }

    // A user cap without a budget keeps its proportional behaviour.
    do {
        let cap = ContextPolicy(contextWindow: 32_000, isUserCap: true, autoCompactBudgetTokens: 0)
        h.expectEq("policy.legacy_user_cap_untouched", cap.compactThreshold, 27_200, "85% of cap")
        h.expectEq("policy.legacy_user_cap_untouched", cap.offloadThreshold, 22_400, "70% of cap")
        h.expectEq("policy.legacy_user_cap_untouched", cap.offloadTarget, 17_600, "55% of cap")
        h.expect("policy.legacy_user_cap_untouched", !cap.exhaustedOnly && cap.manualCompactAllowed)
        let cap2 = ContextPolicy(contextWindow: 200_000, isUserCap: true, autoCompactBudgetTokens: 0)
        h.expectEq("policy.legacy_user_cap_untouched", cap2.compactThreshold, 170_000)
        h.expectEq("policy.legacy_user_cap_untouched", cap2.offloadThreshold, 140_000)
        h.expectEq("policy.legacy_user_cap_untouched", cap2.offloadTarget, 110_000)
    }
}

// MARK: - check() hard ceiling and in-loop guard (real window stays in charge)

private func runCheckAndLoopGuardChecks(_ h: BudgetHarness) {
    // The soft budget changes WHEN we compact; it must not touch the REAL
    // window's hard stop: at or beyond the window the result is never `.ok`
    // with or without a budget.
    do {
        let soft = ContextPolicy(contextWindow: 1_050_000, autoCompactBudgetTokens: 256_000)
        h.expectEq("policy.check_hard_ceiling_real_window",
                   resultName(soft.check(estimatedTokens: 1_050_000, contextWindow: 1_050_000)),
                   "needsCompact", "at the real window (soft budget on)")
        h.expectEq("policy.check_hard_ceiling_real_window",
                   resultName(soft.check(estimatedTokens: 1_100_000, contextWindow: 1_050_000)),
                   "needsCompact", "beyond the real window (soft budget on)")

        let native = ContextPolicy(contextWindow: 1_050_000, autoCompactBudgetTokens: 0)
        h.expectEq("policy.check_hard_ceiling_real_window",
                   resultName(native.check(estimatedTokens: 1_029_999, contextWindow: 1_050_000)),
                   "ok", "no budget: below the native line sends")
        h.expectEq("policy.check_hard_ceiling_real_window",
                   resultName(native.check(estimatedTokens: 1_030_000, contextWindow: 1_050_000)),
                   "needsCompact", "no budget: native compact line")
        h.expectEq("policy.check_hard_ceiling_real_window",
                   resultName(native.check(estimatedTokens: 1_050_000, contextWindow: 1_050_000)),
                   "needsCompact", "no budget: the ceiling still routes to compact")

        // Exhausted-only tiers keep their ceiling semantics too.
        let tiny = ContextPolicy(contextWindow: 16_000)
        h.expectEq("policy.check_hard_ceiling_real_window",
                   resultName(tiny.check(estimatedTokens: 14_399, contextWindow: 16_000)), "ok")
        h.expectEq("policy.check_hard_ceiling_real_window",
                   resultName(tiny.check(estimatedTokens: 14_400, contextWindow: 16_000)), "exhausted")
        h.expectEq("policy.check_hard_ceiling_real_window",
                   resultName(tiny.check(estimatedTokens: 16_000, contextWindow: 16_000)), "exhausted",
                   "manual compact not allowed at <32K")
        let small = ContextPolicy(contextWindow: 32_000)
        h.expectEq("policy.check_hard_ceiling_real_window",
                   resultName(small.check(estimatedTokens: 21_999, contextWindow: 32_000)), "ok")
        h.expectEq("policy.check_hard_ceiling_real_window",
                   resultName(small.check(estimatedTokens: 22_000, contextWindow: 32_000)), "exhausted")
        h.expectEq("policy.check_hard_ceiling_real_window",
                   resultName(small.check(estimatedTokens: 32_000, contextWindow: 32_000)), "needsCompact")
    }

    // inLoopStep: the max-count / no-progress protection is `canCompact`,
    // and the stop boundary is the REAL window — never the soft budget. The
    // soft budget may. therefore, never stop a turn while the window fits.
    do {
        h.expectEq("policy.inloop_guard",
                   stepName(ContextPolicy.inLoopStep(verdict: .ok, measured: 999_999, rawTokens: 999_999,
                                                     window: 1_050_000, canCompact: false, ratio: 1.0,
                                                     uncalibratedSendUsed: false)), "proceed")
        h.expectEq("policy.inloop_guard",
                   stepName(ContextPolicy.inLoopStep(verdict: .exhausted, measured: 10, rawTokens: 10,
                                                     window: 1_050_000, canCompact: true, ratio: 1.0,
                                                     uncalibratedSendUsed: false)), "stop")
        h.expectEq("policy.inloop_guard",
                   stepName(ContextPolicy.inLoopStep(verdict: .needsCompact, measured: 300_000, rawTokens: 300_000,
                                                     window: 1_050_000, canCompact: true, ratio: 1.0,
                                                     uncalibratedSendUsed: false)), "compact",
                   "with compaction budget left and progress")
        h.expectEq("policy.inloop_guard",
                   stepName(ContextPolicy.inLoopStep(verdict: .needsCompact, measured: 217_600, rawTokens: 217_600,
                                                     window: 1_050_000, canCompact: false, ratio: 1.0,
                                                     uncalibratedSendUsed: false)), "sendWithinWindow",
                   "soft line reached but window fits: no compact left, still sendable")
        h.expectEq("policy.inloop_guard",
                   stepName(ContextPolicy.inLoopStep(verdict: .needsCompact, measured: 500_000, rawTokens: 500_000,
                                                     window: 1_050_000, canCompact: false, ratio: 1.0,
                                                     uncalibratedSendUsed: false)), "sendWithinWindow")
        h.expectEq("policy.inloop_guard",
                   stepName(ContextPolicy.inLoopStep(verdict: .needsCompact, measured: 1_049_999, rawTokens: 1_049_999,
                                                     window: 1_050_000, canCompact: false, ratio: 1.0,
                                                     uncalibratedSendUsed: true)), "sendWithinWindow",
                   "one token below the real window")
        h.expectEq("policy.inloop_guard",
                   stepName(ContextPolicy.inLoopStep(verdict: .needsCompact, measured: 1_050_000, rawTokens: 1_050_000,
                                                     window: 1_050_000, canCompact: false, ratio: 1.0,
                                                     uncalibratedSendUsed: false)), "stop",
                   "exactly at the real window, calibrated, nothing left")
        h.expectEq("policy.inloop_guard",
                   stepName(ContextPolicy.inLoopStep(verdict: .needsCompact, measured: 1_050_000, rawTokens: 1_049_999,
                                                     window: 1_050_000, canCompact: false, ratio: 1.01,
                                                     uncalibratedSendUsed: false)), "sendUncalibratedOnce")
        h.expectEq("policy.inloop_guard",
                   stepName(ContextPolicy.inLoopStep(verdict: .needsCompact, measured: 1_050_000, rawTokens: 1_049_999,
                                                     window: 1_050_000, canCompact: false, ratio: 1.01,
                                                     uncalibratedSendUsed: true)), "stop",
                   "the uncalibrated send is a one-shot")
        h.expectEq("policy.inloop_guard",
                   stepName(ContextPolicy.inLoopStep(verdict: .needsCompact, measured: 1_100_000, rawTokens: 1_100_000,
                                                     window: 1_050_000, canCompact: false, ratio: 1.01,
                                                     uncalibratedSendUsed: false)), "stop",
                   "raw already over the window")
        h.expectEq("policy.inloop_guard",
                   stepName(ContextPolicy.inLoopStep(verdict: .needsCompact, measured: 9_999_999, rawTokens: 9_999_999,
                                                     window: 0, canCompact: false, ratio: 1.5,
                                                     uncalibratedSendUsed: false)), "sendWithinWindow",
                   "unknown window (0) never stops a request")
    }

    // Sweep: with canCompact == false (max count reached / no progress) the
    // guard must neither re-compact nor hard-stop below the real window.
    for measured in stride(from: 217_600, through: 1_049_999, by: 111_111) {
        let step = ContextPolicy.inLoopStep(verdict: .needsCompact, measured: measured, rawTokens: measured,
                                            window: 1_050_000, canCompact: false, ratio: 1.0,
                                            uncalibratedSendUsed: true)
        h.expect("policy.inloop_no_stop_below_window", step == .sendWithinWindow, "measured \(measured)")
        h.expect("policy.inloop_no_stop_below_window", step != .compact,
                 "measured \(measured): no re-compact without budget/progress")
    }

    // A budget under a small native window (48K tier): the soft target lifts
    // the tier restriction and keeps compaction available — the feature's
    // purpose — while lines stay proportional and below the window.
    do {
        let p = ContextPolicy(contextWindow: 48_000, autoCompactBudgetTokens: 32_000)
        h.expectEq("policy.soft_budget_small_window", p.compactThreshold, 27_200)
        h.expectEq("policy.soft_budget_small_window", p.offloadThreshold, 22_400)
        h.expectEq("policy.soft_budget_small_window", p.offloadTarget, 17_600)
        h.expect("policy.soft_budget_small_window", !p.exhaustedOnly && p.manualCompactAllowed)
        h.expectEq("policy.soft_budget_small_window",
                   resultName(p.check(estimatedTokens: 27_200, contextWindow: 48_000)), "needsCompact")
        h.expectEq("policy.soft_budget_small_window",
                   resultName(p.check(estimatedTokens: 27_199, contextWindow: 48_000)), "ok")
    }

    // Below 32K a valid budget can never be smaller than the window (min
    // budget 32000), so the tiny-model "no auto compact" safety stays intact.
    for b in [32_000, 256_000, 4_000_000] as [Int] {
        let p = ContextPolicy(contextWindow: 16_000, autoCompactBudgetTokens: b)
        h.expect("policy.soft_budget_never_applies_below_min_window",
                 p.exhaustedOnly && p.compactThreshold == 0 && !p.manualCompactAllowed,
                 "window 16000 budget \(b)")
    }
}

// MARK: - Independent expectation table (re-derived by the python oracle)
//
// Each line is parsed by scripts/test_auto_compaction_budget.py and recomputed
// with exact rational arithmetic from the plan spec. kind is derived by the
// oracle: soft when 0 < budget < window and budget is valid; user cap when
// iscap; otherwise the legacy tier table.
// @case window=1050000 budget=256000 iscap=false compact=217600 offload=179200 target=140800
// @case window=1050000 budget=32000 iscap=false compact=27200 offload=22400 target=17600
// @case window=1050000 budget=64000 iscap=false compact=54400 offload=44800 target=35200
// @case window=1050000 budget=128000 iscap=false compact=108800 offload=89600 target=70400
// @case window=1050000 budget=512000 iscap=false compact=435200 offload=358400 target=281600
// @case window=2048000 budget=1000000 iscap=false compact=850000 offload=700000 target=550000
// @case window=1050000 budget=1050000 iscap=false compact=1030000 offload=1010000 target=990000
// @case window=1050000 budget=4000000 iscap=false compact=1030000 offload=1010000 target=990000
// @case window=200000 budget=4000000 iscap=true compact=170000 offload=140000 target=110000
// @case window=1050000 budget=256000 iscap=true compact=217600 offload=179200 target=140800
// @case window=32000 budget=0 iscap=true compact=27200 offload=22400 target=17600
// @case window=200000 budget=0 iscap=true compact=170000 offload=140000 target=110000
// @case window=48000 budget=32000 iscap=false compact=27200 offload=22400 target=17600
// @case window=16000 budget=0 iscap=false compact=0 offload=0 target=0
// @case window=31999 budget=0 iscap=false compact=0 offload=0 target=0
// @case window=32000 budget=0 iscap=false compact=0 offload=22000 target=17000
// @case window=63999 budget=0 iscap=false compact=0 offload=53999 target=48999
// @case window=64000 budget=0 iscap=false compact=54000 offload=44000 target=34000
// @case window=127999 budget=0 iscap=false compact=117999 offload=107999 target=97999
// @case window=128000 budget=0 iscap=false compact=108000 offload=88000 target=68000
// @case window=1050000 budget=0 iscap=false compact=1030000 offload=1010000 target=990000
// @case window=1050000 budget=1 iscap=false compact=1030000 offload=1010000 target=990000
// @case window=1050000 budget=31999 iscap=false compact=1030000 offload=1010000 target=990000
// @case window=1050000 budget=4000001 iscap=false compact=1030000 offload=1010000 target=990000
// @case window=1050000 budget=-1 iscap=false compact=1030000 offload=1010000 target=990000

@main
struct AutoCompactionBudgetTestsMain {
    static func main() {
        let h = BudgetHarness()
        runPreferenceChecks(h)
        runPersistedValueChecks(h)
        runStandardPreferenceChecks(h)
        runSoftBudgetPolicyChecks(h)
        runLegacyTierChecks(h)
        runCheckAndLoopGuardChecks(h)
        print("SUMMARY pass=\(h.pass) fail=\(h.fail) skip=\(h.skip)")
        exit(h.fail == 0 ? 0 : 1)
    }
}
