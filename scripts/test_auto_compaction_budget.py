#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Standalone regression runner for [T-auto-compact-budget] (soft auto-compaction budget).

Scope: the pure production types `AutoCompactionPreferences` + `ContextPolicy`
(src/ios/Agent/Chat/ContextPolicy.swift) and the *source wiring* in
AIChatViewModel{,+Compaction,+Offloading,+Persistence}.swift. It does NOT
start the app, does not touch the network, the device, user defaults files of
the real app, Hosts/priority/installation state, and never writes into the
source tree (mutants run in a throwaway mirror under /tmp).

Evidence classes (each check prints its class):
  [extract]  byte-preserving extraction of the production unit (static proof;
             sha256 of the extracted bytes is recorded)
  [native]   REAL execution: `swiftc -parse-as-library <extracted>.swift <test>.swift`
             then run. Missing swiftc => SKIP (exit 77 overall), never a pass.
  [static]   tree-sitter source-wiring checks against the production bytes
             (structure, not execution — never claimed as "production ran")
  [oracle]   independent spec oracle: the `// @case` rows embedded in the
             Swift test file are recomputed with exact rational arithmetic
             (Fraction) from the plan spec and must agree (static-only)
  [catalog] / [view]  advisory contract checks for the settings page +
             Localizable.xcstrings (owned by the parallel UI task; PENDING
             until those artifacts land)
  [mutant]   real mutants applied in a tmp mirror; each must be REJECTED by a
             named SEMANTIC assertion (a mutant that merely fails to parse is
             reported INVALID, never as a rejection)

Exit codes:
  0  all executed checks passed and the native suite ran (if a runner exists)
  77 static-only: every executed check passed, but the native suite was
     SKIPped because no Swift toolchain is available (NOT a native pass)
  1  at least one executed check failed, or a strict mutant was not rejected

Usage:
  python3 scripts/test_auto_compaction_budget.py
  python3 scripts/test_auto_compaction_budget.py --source-root /tmp/mirror --runner /usr/bin/swiftc
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

try:
    from tree_sitter import Language, Parser
    import tree_sitter_swift
except Exception as exc:  # tree-sitter is a hard requirement of the static half
    print("FATAL: tree_sitter / tree_sitter_swift unavailable: %r" % (exc,))
    sys.exit(3)

PARSER = Parser(Language(tree_sitter_swift.language()))

CHAT = "src/ios/Agent/Chat"
POLICY_REL = CHAT + "/ContextPolicy.swift"
COMPACTION_REL = CHAT + "/AIChatViewModel+Compaction.swift"
OFFLOAD_REL = CHAT + "/AIChatViewModel+Offloading.swift"
PERSIST_REL = CHAT + "/AIChatViewModel+Persistence.swift"
VM_REL = CHAT + "/AIChatViewModel.swift"
CHAT_FILES = [POLICY_REL, COMPACTION_REL, OFFLOAD_REL, PERSIST_REL, VM_REL]
TEST_REL = "tests/Standalone/AutoCompactionBudgetTests.swift"
PBX_REL = "src/ios/Minis.xcodeproj/project.pbxproj"
CATALOG_REL = "src/ios/Localizable.xcstrings"
VIEWS_REL = "src/ios/Views"
EXTRACT_END = "// MARK: - Outbound context measurement"

LEGACY_KEY = "autoCompactOnThreshold"
BUDGET_KEY = "autoCompactBudgetTokens"

STATUS_ORDER = {"FAIL": 0, "PASS": 1, "PENDING": 2, "SKIP": 3, "INFO": 4}


class Report:
    def __init__(self):
        self.items = []

    def add(self, cls, name, status, detail=""):
        self.items.append({"cls": cls, "name": name, "status": status, "detail": detail})
        return status

    def by_class(self, cls):
        return [i for i in self.items if i["cls"] == cls]

    def has_fail(self):
        return any(i["status"] == "FAIL" for i in self.items)

    def status_of(self, name):
        for i in self.items:
            if i["name"] == name:
                return i["status"]
        return None

    def summarize(self):
        counts = {}
        for i in self.items:
            counts[i["status"]] = counts.get(i["status"], 0) + 1
        return counts


def walk(node):
    yield node
    for child in node.children:
        yield from walk(child)


class SwiftFile:
    def __init__(self, path, rel):
        self.path = Path(path)
        self.rel = rel
        self.raw = self.path.read_bytes()
        self.text = self.raw.decode("utf-8")
        self.tree = PARSER.parse(self.raw)
        self.error_nodes = [n for n in walk(self.tree.root_node)
                            if n.type == "ERROR" or getattr(n, "is_missing", False)]

    def t(self, node):
        return self.raw[node.start_byte:node.end_byte].decode("utf-8")

    def line_of(self, node):
        return self.raw[:node.start_byte].count(b"\n") + 1

    def nodes(self, ntype):
        return [n for n in walk(self.tree.root_node) if n.type == ntype]

    def function_name(self, node):
        for c in node.children:
            if c.type == "simple_identifier":
                return self.t(c)
        return None

    def functions(self):
        out = {}
        for n in self.nodes("function_declaration"):
            out.setdefault(self.function_name(n), []).append(n)
        return out

    def enclosing_function(self, node):
        n = node
        while n is not None:
            if n.type in ("function_declaration", "init_declaration"):
                return n
            n = n.parent
        return None

    def error_nodes_within(self, start, end):
        return [e for e in self.error_nodes if e.start_byte >= start and e.end_byte <= end]


def call_args(sf, call_node):
    """Return [(label|None, value_text)] for a call_expression node."""
    args = []
    for child in call_node.children:
        if child.type != "call_suffix":
            continue
        for sub in child.children:
            if sub.type != "value_arguments":
                continue
            for a in sub.children:
                if a.type != "value_argument":
                    continue
                txt = sf.t(a)
                if ":" in txt:
                    label, value = txt.split(":", 1)
                    args.append((label.strip(), value.strip()))
                else:
                    args.append((None, txt.strip()))
    return args


SIMPLE_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def binding_from(sf, func_node, ident, needle):
    """True if `let/var <ident> = ... <needle>` appears in the function text."""
    if func_node is None:
        return False
    body = sf.t(func_node)
    pat = re.compile(r"\b(?:let|var)\s+%s\s*=\s*[^\n]*%s" % (re.escape(ident), re.escape(needle)))
    return bool(pat.search(body))


def balanced_block(text, start_brace):
    depth = 0
    for i in range(start_brace, len(text)):
        ch = text[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start_brace:i + 1]
    return None


def sha256_hex(data):
    return hashlib.sha256(data).hexdigest()

# ---------------------------------------------------------------------------
# [extract] byte-preserving production extraction
# ---------------------------------------------------------------------------

def extract_production_unit(policy_sf):
    """Everything before the ContextSizeMeter MARK (import + preferences + policy)."""
    idx = policy_sf.text.find(EXTRACT_END)
    if idx < 0:
        return None, "anchor %r not found" % EXTRACT_END
    cut_text = policy_sf.text[:idx]
    cut = cut_text.encode("utf-8")
    if not policy_sf.raw.startswith(cut):
        return None, "extraction is not a byte-exact prefix of the production file"
    return cut, None


def check_extraction(ss, rep):
    sf = ss.files.get(POLICY_REL)
    if sf is None:
        rep.add("extract", "extract.production_boundary", "FAIL", "missing " + POLICY_REL)
        return None
    cut, err = extract_production_unit(sf)
    if cut is None:
        rep.add("extract", "extract.production_boundary", "FAIL", err)
        return None
    text = cut.decode("utf-8")
    problems = []
    for needle in ("enum AutoCompactionPreferences", "struct ContextPolicy",
                   "struct ContextPolicy {\n    /// Tokens used must exceed"):
        if needle not in text:
            problems.append("missing %r" % needle[:40])
    for forbidden in ("enum ContextSizeMeter", "AgentMessage", "LLMModel", "/MinisTests/"):
        if forbidden in text:
            problems.append("leaked %r (extraction should stop before ContextSizeMeter)" % forbidden)
    sub = PARSER.parse(cut)
    errs = [n for n in walk(sub.root_node) if n.type == "ERROR"]
    if errs:
        problems.append("extracted unit does not parse cleanly (%d ERROR nodes)" % len(errs))
    if problems:
        rep.add("extract", "extract.production_boundary", "FAIL", "; ".join(problems))
        return None
    rep.add("extract", "extract.production_boundary", "PASS",
            "sha256=%s bytes=%d lines=%d" % (sha256_hex(cut), len(cut), text.count("\n") + 1))
    return cut


# ---------------------------------------------------------------------------
# [static] source-wiring checks (tree-sitter, production bytes)
# ---------------------------------------------------------------------------

def _policy_call_nodes(sf):
    return [n for n in sf.nodes("call_expression") if sf.t(n).startswith("ContextPolicy(")]


def _budget_arg_ok(sf, call_node, args):
    values = [v for label, v in args if label == "autoCompactBudgetTokens"]
    if not values:
        return False, "constructed without an autoCompactBudgetTokens argument"
    value = values[0]
    if "AutoCompactionPreferences.activeBudgetTokens" in value:
        return True, "activeBudgetTokens passed inline"
    if SIMPLE_IDENT.match(value) and binding_from(sf, sf.enclosing_function(call_node), value,
                                                  "AutoCompactionPreferences.activeBudgetTokens"):
        return True, "activeBudgetTokens via local %r" % value
    return False, "budget argument is not AutoCompactionPreferences.activeBudgetTokens (%r)" % value[:60]


def check_policy_call_sites(ss, rep, rel, check_name, min_count):
    sf = ss.files.get(rel)
    if sf is None:
        rep.add("static", check_name, "FAIL", "missing " + rel)
        return
    calls = _policy_call_nodes(sf)
    if len(calls) < min_count:
        rep.add("static", check_name, "FAIL",
                "expected >= %d ContextPolicy( sites, found %d" % (min_count, len(calls)))
        return
    bad = []
    for call in calls:
        ok, why = _budget_arg_ok(sf, call, call_args(sf, call))
        if not ok:
            bad.append("line %d: %s" % (sf.line_of(call), why))
    if bad:
        rep.add("static", check_name, "FAIL", "; ".join(bad))
    else:
        rep.add("static", check_name, "PASS", "%d/%d sites carry the budget" % (len(calls), len(calls)))


def check_all_policy_sites(ss, rep):
    """Repo-wide: every ContextPolicy( call in src/ios (excluding tests) carries the budget."""
    root = ss.root / "src" / "ios"
    offenders = []
    total = 0
    for path in sorted(root.rglob("*.swift")):
        rel = str(path.relative_to(ss.root))
        if "MinisTests" in rel or rel == POLICY_REL:
            continue
        try:
            raw = path.read_bytes()
        except OSError:
            continue
        if b"ContextPolicy(" not in raw:
            continue
        sf = SwiftFile(path, rel)
        for call in _policy_call_nodes(sf):
            total += 1
            ok, why = _budget_arg_ok(sf, call, call_args(sf, call))
            if not ok:
                offenders.append("%s:%d %s" % (rel, sf.line_of(call), why))
    if offenders:
        rep.add("static", "wiring.all_policy_sites_pass_budget", "FAIL", "; ".join(offenders))
    else:
        rep.add("static", "wiring.all_policy_sites_pass_budget", "PASS",
                "%d ContextPolicy( site(s) outside tests, all budgeted" % total)


def check_check_call_real_window(ss, rep):
    check_name = "wiring.compaction.check_uses_real_window"
    sf = ss.files.get(COMPACTION_REL)
    if sf is None:
        rep.add("static", check_name, "FAIL", "missing " + COMPACTION_REL)
        return
    calls = [n for n in sf.nodes("call_expression")
             if re.match(r"policy\.check\(", sf.t(n))]
    if not calls:
        rep.add("static", check_name, "FAIL", "no policy.check( call found")
        return
    problems = []
    for call in calls:
        args = dict((l, v) for l, v in call_args(sf, call) if l)
        value = args.get("contextWindow")
        if value is None:
            problems.append("line %d: no contextWindow argument" % sf.line_of(call))
            continue
        if "udget" in value or "AutoCompaction" in value:
            problems.append("line %d: contextWindow argument references the budget (%r)"
                            % (sf.line_of(call), value[:60]))
            continue
        if SIMPLE_IDENT.match(value):
            if not binding_from(sf, sf.enclosing_function(call), value, ".window"):
                problems.append("line %d: %r is not bound from a resolved .window"
                                % (sf.line_of(call), value))
        elif ".window" not in value:
            problems.append("line %d: contextWindow argument %r is not the real window"
                            % (sf.line_of(call), value[:60]))
    if problems:
        rep.add("static", check_name, "FAIL", "; ".join(problems))
    else:
        rep.add("static", check_name, "PASS",
                "hard ceiling still judged against the resolved real window (%d site(s))" % len(calls))


def check_inloop_real_window(ss, rep):
    check_name = "wiring.compaction.inloop_uses_real_window"
    sf = ss.files.get(COMPACTION_REL)
    if sf is None:
        rep.add("static", check_name, "FAIL", "missing " + COMPACTION_REL)
        return
    calls = [n for n in sf.nodes("call_expression")
             if "ContextPolicy.inLoopStep(" in sf.t(n)]
    if not calls:
        rep.add("static", check_name, "FAIL", "no ContextPolicy.inLoopStep( call found")
        return
    problems = []
    for call in calls:
        args = dict((l, v) for l, v in call_args(sf, call) if l)
        value = args.get("window")
        if value is None:
            problems.append("line %d: no window argument" % sf.line_of(call))
            continue
        if "udget" in value or "AutoCompaction" in value:
            problems.append("line %d: window argument references the budget (%r)"
                            % (sf.line_of(call), value[:60]))
            continue
        if SIMPLE_IDENT.match(value):
            if not binding_from(sf, sf.enclosing_function(call), value, ".window"):
                problems.append("line %d: %r is not bound from a resolved .window"
                                % (sf.line_of(call), value))
        elif ".window" not in value:
            problems.append("line %d: window argument %r is not the real window"
                            % (sf.line_of(call), value[:60]))
    if problems:
        rep.add("static", check_name, "FAIL", "; ".join(problems))
    else:
        rep.add("static", check_name, "PASS",
                "in-loop guard window is the resolved real window (%d site(s))" % len(calls))

def check_persistence_invalidation(ss, rep):
    check_name = "wiring.persistence.budget_change_invalidates_warmup"
    sf = ss.files.get(PERSIST_REL)
    if sf is None:
        rep.add("static", check_name, "FAIL", "missing " + PERSIST_REL)
        return
    funcs = sf.functions().get("effectiveAgentHistoryUncounted") or []
    if not funcs:
        rep.add("static", check_name, "FAIL", "effectiveAgentHistoryUncounted() not found")
        return
    body = sf.t(funcs[0])
    parts = {
        "reads activeBudgetTokens": "AutoCompactionPreferences.activeBudgetTokens" in body,
        "compares stored budget": bool(re.search(r"warmUpCompactionBudgetTokens\s*!=", body)
                                       or re.search(r"!=\s*warmUpCompactionBudgetTokens", body)
                                       or re.search(r"warmUpCompactionBudgetTokens\s*==", body)),
        "clears warmUpDropByMarker": "warmUpDropByMarker.removeAll()" in body,
        "updates stored budget": bool(re.search(r"warmUpCompactionBudgetTokens\s*=", body)),
    }
    missing = [k for k, v in parts.items() if not v]
    if missing:
        rep.add("static", check_name, "FAIL",
                "budget-change invalidation incomplete: missing " + ", ".join(missing))
    else:
        rep.add("static", check_name, "PASS",
                "budget change clears the warm-up drop cache before reuse")


def check_vm_auto_compact_live(ss, rep):
    check_name = "wiring.vm.auto_compact_enabled_live_read"
    sf = ss.files.get(VM_REL)
    if sf is None:
        rep.add("static", check_name, "FAIL", "missing " + VM_REL)
        return
    idx = sf.text.find("var autoCompactEnabled: Bool")
    if idx < 0:
        rep.add("static", check_name, "FAIL", "autoCompactEnabled declaration not found")
        return
    brace = sf.text.find("{", idx)
    block = balanced_block(sf.text, brace) if brace > 0 else None
    if not block:
        rep.add("static", check_name, "FAIL", "autoCompactEnabled has no computed body")
        return
    problems = []
    if "@Published var autoCompactEnabled" in sf.text:
        problems.append("still @Published (stored/cached) instead of reading at decision time")
    if "didSet" in block:
        problems.append("didSet caching still present")
    if "AutoCompactionPreferences.enabled" not in block:
        problems.append("getter does not read AutoCompactionPreferences.enabled")
    if "AutoCompactionPreferences.enabledKey" not in block:
        problems.append("setter does not persist through AutoCompactionPreferences.enabledKey")
    for rel in (COMPACTION_REL, OFFLOAD_REL, PERSIST_REL, VM_REL):
        other = ss.files.get(rel)
        if other and re.search(r'forKey:\s*"%s"' % LEGACY_KEY, other.text):
            problems.append("%s hardcodes the legacy key literal instead of the shared constant" % rel)
    if problems:
        rep.add("static", check_name, "FAIL", "; ".join(problems))
    else:
        rep.add("static", check_name, "PASS",
                "computed property reads the persisted opt-in at decision time (shared key)")


def check_prefs_static(ss, rep):
    sf = ss.files.get(POLICY_REL)
    if sf is None:
        for n in ("static.prefs.legacy_enabled_key", "static.prefs.type_strict_reader",
                  "static.prefs.active_budget_gated_on_enabled"):
            rep.add("static", n, "FAIL", "missing " + POLICY_REL)
        return

    # legacy key value
    m = re.search(r'static\s+let\s+enabledKey\s*=\s*"([^"]*)"', sf.text)
    if m and m.group(1) == LEGACY_KEY:
        rep.add("static", "static.prefs.legacy_enabled_key", "PASS", 'enabledKey == "%s"' % LEGACY_KEY)
    else:
        rep.add("static", "static.prefs.legacy_enabled_key", "FAIL",
                'enabledKey is %r, expected "%s"' % (m.group(1) if m else None, LEGACY_KEY))

    # type-strict budget reader
    readers = [n for n, fns in sf.functions().items() if n == "budgetTokens"]
    body = ""
    for node in (sf.functions().get("budgetTokens") or []):
        t = sf.t(node)
        if "defaults" in t:
            body = t
            break
    problems = []
    if not body:
        problems.append("budgetTokens(in:) reader not found")
    else:
        if "as? NSNumber" not in body:
            problems.append("no NSNumber type check on the persisted value")
        if "objCType" not in body or '"c"' not in body:
            problems.append("no Boolean-rejection guard (objCType == 'c' check)")
        if re.search(r'(?:defaults\.)?(?:integer|double)\(forKey:', re.sub(r'//[^\n]*', '', body)):
            problems.append("uses lenient integer/double(forKey:) coercion")
    if problems:
        rep.add("static", "static.prefs.type_strict_reader", "FAIL", "; ".join(problems))
    else:
        rep.add("static", "static.prefs.type_strict_reader", "PASS",
                "corrupt string/Boolean persisted values cannot slip through as budgets")

    # activeBudgetTokens gated on enabled
    idx = sf.text.find("static var activeBudgetTokens")
    brace = sf.text.find("{", idx) if idx >= 0 else -1
    block = balanced_block(sf.text, brace) if brace > 0 else None
    if block and "enabled" in block and "budgetTokens" in block and "0" in block:
        rep.add("static", "static.prefs.active_budget_gated_on_enabled", "PASS",
                "activeBudgetTokens is enabled ? budgetTokens : 0")
    else:
        rep.add("static", "static.prefs.active_budget_gated_on_enabled", "FAIL",
                "activeBudgetTokens does not gate the budget on `enabled`")


def check_policy_static(ss, rep):
    sf = ss.files.get(POLICY_REL)
    if sf is None:
        for n in ("static.policy.init_budget_param_default0", "static.policy.soft_budget_condition_guards",
                  "static.policy.proportional_percentages"):
            rep.add("static", n, "FAIL", "missing " + POLICY_REL)
        return

    # init signature keeps the new parameter optional => old call sites compile.
    m = re.search(r"init\s*\(\s*contextWindow:\s*Int\s*,\s*isUserCap:\s*Bool\s*=\s*false\s*,"
                  r"\s*autoCompactBudgetTokens:\s*Int\s*=\s*0\s*\)", sf.text)
    rep.add("static", "static.policy.init_budget_param_default0",
            "PASS" if m else "FAIL",
            "init(contextWindow:isUserCap:autoCompactBudgetTokens: = 0) present" if m
            else "budget parameter missing/not defaulted to 0 (old call sites would not compile)")

    idx = sf.text.find("let hasSoftBudget")
    cond = ""
    if idx >= 0:
        tail = sf.text[idx:idx + 500]
        stop = re.search(r"\n\s*(?:let|if|guard|var|return)\b", tail[18:])
        cond = re.sub(r"\s+", " ", tail if stop is None else tail[:18 + stop.start()])
    problems = []
    if not cond:
        problems.append("hasSoftBudget binding not found")
    else:
        if "AutoCompactionPreferences.isValidBudget(autoCompactBudgetTokens)" not in cond:
            problems.append("no isValidBudget precondition")
        if "autoCompactBudgetTokens > 0" not in cond:
            problems.append("no `> 0` guard (0 must keep meaning follow-the-window)")
        if "autoCompactBudgetTokens < contextWindow" not in cond:
            problems.append("no `< contextWindow` guard (budget must never raise the line)")
    if problems:
        rep.add("static", "static.policy.soft_budget_condition_guards", "FAIL", "; ".join(problems))
    else:
        rep.add("static", "static.policy.soft_budget_condition_guards", "PASS",
                "soft budget only when valid, > 0 and strictly below the real window")

    idx = sf.text.find("if isUserCap || hasSoftBudget")
    branch = ""
    if idx >= 0:
        end = sf.text.find("return", idx)
        branch = re.sub(r"\s+", " ", sf.text[idx:end]) if end > idx else ""
    missing = [p for p in ("0.85", "0.70", "0.55", "policyWindow") if p not in branch]
    if idx < 0 or missing:
        rep.add("static", "static.policy.proportional_percentages", "FAIL",
                "proportional branch incomplete: missing " + ", ".join(missing or ["if isUserCap || hasSoftBudget"]))
    else:
        rep.add("static", "static.policy.proportional_percentages", "PASS",
                "85% / 70% / 55% of policyWindow in the proportional branch")


def check_catalog(ss, rep):
    """Advisory: Localizable.xcstrings entries for the new page (parallel UI task)."""
    path = ss.root / CATALOG_REL
    if not path.exists():
        rep.add("catalog", "catalog.auto_compact_keys", "PENDING", CATALOG_REL + " not found")
        return
    try:
        data = json.loads(path.read_text())
        strings = data.get("strings", {})
    except Exception as exc:
        rep.add("catalog", "catalog.auto_compact_keys", "FAIL", "xcstrings does not parse: %r" % (exc,))
        return
    hits = []
    for key, entry in strings.items():
        blob = key + " " + json.dumps(entry, ensure_ascii=False)
        if re.search(r"auto[\s-]?compact|autocompact|自动压缩", blob, re.IGNORECASE):
            hits.append(key)
    if not hits:
        rep.add("catalog", "catalog.auto_compact_keys", "PENDING",
                "no auto-compaction strings yet (parallel UI/localization task not landed)")
        return
    bad = []
    for key in hits[:40]:
        locs = strings[key].get("localizations", {})
        for lang in ("en", "zh-Hans"):
            unit = ((locs.get(lang) or {}).get("stringUnit") or {}).get("value", "")
            if not unit:
                bad.append("%s missing %s" % (key[:60], lang))
    if bad:
        rep.add("catalog", "catalog.auto_compact_keys", "FAIL", "; ".join(bad))
    else:
        rep.add("catalog", "catalog.auto_compact_keys", "PASS",
                "%d key(s) with en+zh-Hans values" % len(hits))


def check_new_view_contract(ss, rep):
    """Advisory: the auto-compaction settings page exists and is reachable."""
    views_root = ss.root / VIEWS_REL
    view_files = []
    if views_root.exists():
        for p in sorted(views_root.rglob("*.swift")):
            try:
                txt = p.read_text(errors="replace")
            except OSError:
                continue
            if re.search(r"struct\s+\w*(?:AutoCompact|AutoCompaction)\w*\s*:\s*View", txt):
                view_files.append((p, txt))
    if not view_files:
        rep.add("view", "view.auto_compaction_settings_page", "PENDING",
                "no AutoCompaction settings view yet (parallel UI task not landed)")
        return
    path, txt = view_files[0]
    rep.add("view", "view.auto_compaction_settings_page", "PASS", str(path.relative_to(ss.root)))
    # settings entry
    content = (ss.root / "src/ios/Views/ContentView.swift")
    entry_ok = False
    if content.exists():
        ctext = content.read_text(errors="replace")
        entry_ok = bool(re.search(r"NavigationLink[\s\S]{0,400}?(AutoCompact|AutoCompaction)\w*SettingsView", ctext)) \
            or (( "AutoCompactionSettingsView" in ctext or "AutoCompactSettingsView" in ctext))
    rep.add("view", "view.settings_entry", "PASS" if entry_ok else "FAIL",
            "settings list links the page" if entry_ok else "no NavigationLink found in ContentView.swift")
    # chat menu entry
    chat = ss.root / "src/ios/Views/Chat/AIChatView.swift"
    menu_ok = False
    if chat.exists():
        ctext = chat.read_text(errors="replace")
        for m in re.finditer(r"Menu\s*\{", ctext):
            block = balanced_block(ctext, m.end() - 1)
            if block and re.search(r"uto[\s-]?Compact", block, re.IGNORECASE):
                menu_ok = True
                break
    rep.add("view", "view.chat_menu_entry", "PASS" if menu_ok else "FAIL",
            "chat overflow menu exposes auto-compaction" if menu_ok
            else "no auto-compaction control found inside a Menu block in AIChatView.swift")
    # pbxproj registration
    pbx = ss.root / PBX_REL
    base = path.name
    if pbx.exists():
        ptext = pbx.read_text(errors="replace")
        n = ptext.count('"%s"' % base) + ptext.count(base)
        rep.add("view", "view.pbxproj_registration", "PASS" if n >= 2 else "FAIL",
                "%s referenced %d times in project.pbxproj" % (base, n))
    else:
        rep.add("view", "view.pbxproj_registration", "PENDING", PBX_REL + " not found")

# ---------------------------------------------------------------------------
# [oracle] independent spec oracle over the `// @case` table in the Swift test
# ---------------------------------------------------------------------------

from fractions import Fraction

CASE_RE = re.compile(r"^\s*//\s*@case\s+(.+)$", re.M)


def oracle_values(window, budget, iscap):
    """Re-derive the expected policy numbers from the plan spec, independently
    of the Swift implementation: soft budget = 85/70/55% of the budget when it
    is valid, > 0 and strictly below the window; a user cap = same proportions
    of the window; otherwise the legacy tier table (10K/15K, 20K/30K, 40K/60K
    headroom by tier, compact only from the 64K tier up)."""
    valid = (budget == 0) or (32000 <= budget <= 4000000)
    soft = valid and budget > 0 and budget < window
    if soft or iscap:
        base = budget if soft else window
        return {
            "offload": int(Fraction(base) * Fraction(70, 100)),
            "target": int(Fraction(base) * Fraction(55, 100)),
            "compact": int(Fraction(base) * Fraction(85, 100)),
        }
    if window < 32000:
        return {"offload": 0, "target": 0, "compact": 0}
    if window < 64000:
        return {"offload": window - 10000, "target": window - 15000, "compact": 0}
    if window < 128000:
        return {"offload": window - 20000, "target": window - 30000, "compact": window - 10000}
    return {"offload": window - 40000, "target": window - 60000, "compact": window - 20000}


def check_oracle(rep, test_file):
    check_name = "oracle.case_table_recomputed"
    if not test_file.exists():
        rep.add("oracle", check_name, "FAIL", "test file not found: %s" % test_file)
        return
    text = test_file.read_text()
    rows = []
    for m in CASE_RE.finditer(text):
        try:
            kv = dict(pair.split("=", 1) for pair in m.group(1).split())
            rows.append({
                "window": int(kv["window"]), "budget": int(kv["budget"]),
                "iscap": kv["iscap"].strip().lower() == "true",
                "compact": int(kv["compact"]), "offload": int(kv["offload"]),
                "target": int(kv["target"]),
            })
        except Exception as exc:
            rep.add("oracle", check_name, "FAIL", "unparseable @case row %r: %r" % (m.group(1)[:60], exc))
            return
    if len(rows) < 20:
        rep.add("oracle", check_name, "FAIL",
                "only %d @case rows (expected >= 20 for meaningful boundary coverage)" % len(rows))
        return
    mismatches = []
    kinds = set()
    for row in rows:
        exp = oracle_values(row["window"], row["budget"], row["iscap"])
        valid = (row["budget"] == 0) or (32000 <= row["budget"] <= 4000000)
        soft = valid and row["budget"] > 0 and row["budget"] < row["window"]
        kinds.add("soft" if soft else ("cap" if row["iscap"] else "legacy"))
        for field in ("compact", "offload", "target"):
            if row[field] != exp[field]:
                mismatches.append("window=%d budget=%d iscap=%s %s: swift-test=%d oracle=%d"
                                  % (row["window"], row["budget"], row["iscap"], field,
                                     row[field], exp[field]))
    if mismatches:
        rep.add("oracle", check_name, "FAIL", "; ".join(mismatches[:6]))
    else:
        rep.add("oracle", check_name, "PASS",
                "%d rows recomputed with exact rational arithmetic (kinds: %s; static-only, "
                "not an execution of Swift)" % (len(rows), ",".join(sorted(kinds))))


# ---------------------------------------------------------------------------
# [native] real execution via a Swift toolchain (never faked)
# ---------------------------------------------------------------------------

def resolve_runner(explicit):
    if explicit:
        if shutil.which(explicit) or Path(explicit).exists():
            return explicit, None
        return None, "explicit --runner %r not found" % explicit
    found = shutil.which("swiftc")
    if found:
        return found, None
    return None, None


def check_native(ss, rep, runner, test_file, workdir, allow_std, name="native.suite",
                 expect_ids=None):
    """Compile extracted production + the standalone test and run it.

    Returns dict(status=..., fails=set(), output=str). status: PASS/FAIL/SKIP.
    """
    cut, err = extract_production_unit(ss.files[POLICY_REL]) if ss.files.get(POLICY_REL) else (None, "missing")
    if cut is None:
        rep.add("native", name, "FAIL", "cannot extract production unit: %s" % err)
        return {"status": "FAIL", "fails": set()}
    if runner is None:
        rep.add("native", name, "SKIP",
                "no swiftc on PATH (install Xcode CLT / pass --runner); static-only evidence")
        return {"status": "SKIP", "fails": set()}
    if not test_file.exists():
        rep.add("native", name, "FAIL", "test file not found: %s" % test_file)
        return {"status": "FAIL", "fails": set()}

    home = workdir / "home"
    for sub in ("home", "tmp", "xdg"):
        (workdir / sub).mkdir(parents=True, exist_ok=True)
    prod = workdir / "prod_extract.swift"
    prod.write_bytes(cut)
    exe = workdir / "acb_runner"
    if exe.exists():
        exe.unlink()
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["TMPDIR"] = str(workdir / "tmp")
    env["XDG_CONFIG_HOME"] = str(workdir / "xdg")
    env["XDG_DATA_HOME"] = str(workdir / "xdg")
    env["XDG_CACHE_HOME"] = str(workdir / "xdg")
    env["ACB_ALLOW_STANDARD_DEFAULTS"] = "1" if allow_std else "0"

    cmd = [runner, "-parse-as-library", str(prod), str(test_file), "-o", str(exe)]
    try:
        comp = subprocess.run(cmd, capture_output=True, text=True, timeout=900, env=env, cwd=str(workdir))
    except subprocess.TimeoutExpired:
        rep.add("native", name, "FAIL", "swiftc timed out after 900s")
        return {"status": "FAIL", "fails": set()}
    if comp.returncode != 0 or not exe.exists():
        tail = "\n".join(comp.stderr.strip().splitlines()[-12:])
        rep.add("native", name, "FAIL",
                "COMPILE failed (rc=%d; this is not a semantic rejection):\n%s" % (comp.returncode, tail))
        return {"status": "FAIL", "fails": set(), "compile_error": True}

    try:
        proc = subprocess.run([str(exe)], capture_output=True, text=True, timeout=300,
                              env=env, cwd=str(workdir))
    except subprocess.TimeoutExpired:
        rep.add("native", name, "FAIL", "runner timed out after 300s")
        return {"status": "FAIL", "fails": set()}
    out = proc.stdout + ("\n[stderr]\n" + proc.stderr if proc.stderr.strip() else "")
    (workdir / (re.sub(r"[^A-Za-z0-9_.-]", "_", name) + ".stdout.txt")).write_text(out)
    fails = set(re.findall(r"^FAIL\[([^\]]+)\]", proc.stdout, re.M))
    m = re.search(r"SUMMARY pass=(\d+) fail=(\d+) skip=(\d+)", proc.stdout)
    if m is None:
        rep.add("native", name, "FAIL", "no SUMMARY line (runner rc=%d); tail:\n%s"
                % (proc.returncode, "\n".join(proc.stdout.strip().splitlines()[-8:])))
        return {"status": "FAIL", "fails": fails, "output": out}
    p, f, k = (int(x) for x in m.groups())
    detail = "exit=%d pass=%d fail=%d skip=%d" % (proc.returncode, p, f, k)
    if expect_ids:
        missing = sorted(set(expect_ids) - fails)
        if missing:
            detail += "; expected FAIL ids missing: " + ", ".join(missing)
    if proc.returncode == 0 and f == 0:
        rep.add("native", name, "PASS", detail)
        return {"status": "PASS", "fails": fails, "output": out}
    first_fails = [ln for ln in proc.stdout.splitlines() if ln.startswith("FAIL[")][:4]
    rep.add("native", name, "FAIL", detail + ("; " + " | ".join(first_fails) if first_fails else ""))
    return {"status": "FAIL", "fails": fails, "output": out}


class SourceSet:
    def __init__(self, root):
        self.root = Path(root)
        self.files = {rel: SwiftFile(self.root / rel, rel) for rel in CHAT_FILES}


def static_core(ss, rep):
    check_extraction(ss, rep)
    check_policy_call_sites(ss, rep, COMPACTION_REL, "wiring.compaction.compaction_sites_have_budget", 2)
    check_policy_call_sites(ss, rep, OFFLOAD_REL, "wiring.offload.offload_site_has_budget", 1)
    check_all_policy_sites(ss, rep)
    check_check_call_real_window(ss, rep)
    check_inloop_real_window(ss, rep)
    check_persistence_invalidation(ss, rep)
    check_vm_auto_compact_live(ss, rep)
    check_prefs_static(ss, rep)
    check_policy_static(ss, rep)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--source-root', type=Path, default=Path(__file__).resolve().parents[1])
    ap.add_argument('--runner')
    ap.add_argument('--json', type=Path)
    ap.add_argument('--mutants', choices=['none', 'static'], default='static')
    args = ap.parse_args()
    rep = Report(); ss = SourceSet(args.source_root)
    static_core(ss, rep)
    check_oracle(rep, args.source_root / TEST_REL)
    check_catalog(ss, rep)
    check_new_view_contract(ss, rep)
    runner, err = resolve_runner(args.runner)
    if err: rep.add('native', 'native.toolchain', 'FAIL', err)
    with tempfile.TemporaryDirectory(prefix='acb-tests-') as td:
        native = check_native(ss, rep, runner, args.source_root / TEST_REL, Path(td) / 'native', True)
        mutations = [
            (COMPACTION_REL, 'let budget = AutoCompactionPreferences.activeBudgetTokens', 'let budget = 0', 'disabled compaction wiring'),
            (OFFLOAD_REL, 'autoCompactBudgetTokens: AutoCompactionPreferences.activeBudgetTokens)', 'autoCompactBudgetTokens: 0)', 'disabled offload wiring'),
            (POLICY_REL, 'enabled ? budgetTokens : 0', 'budgetTokens', 'ignored off switch'),
            (PERSIST_REL, 'warmUpDropByMarker.removeAll()', '_ = warmUpDropByMarker.count', 'stale warmup cache'),
        ]
        if args.mutants == 'static':
            for idx, (rel, old, new, label) in enumerate(mutations):
                mr = Path(td) / ('m'+str(idx))
                for f in CHAT_FILES:
                    dst=mr/f;dst.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(args.source_root/f,dst)
                target=mr/rel; original=target.read_text()
                if original.count(old)!=1:
                    rep.add('mutant',label,'FAIL','mutation anchor is not unique');continue
                target.write_text(original.replace(old,new,1)); mutated=SourceSet(mr)
                if len(mutated.files[rel].error_nodes)!=len(ss.files[rel].error_nodes):
                    rep.add('mutant',label,'FAIL','INVALID: changed parser error count');continue
                rr=Report();static_core(mutated,rr)
                baseline_failures = {i['name'] for i in rep.items if i['status']=='FAIL' and i['cls']=='static'}
                caught=[i['name'] for i in rr.items if i['status']=='FAIL' and i['cls']=='static'
                        and i['name'] not in baseline_failures]
                rep.add('mutant',label,'PASS' if caught else 'FAIL','parse-valid source rejected by '+','.join(caught))
    for item in rep.items:
        print('[{cls}] {status} {name}: {detail}'.format(**item))
    summary=rep.summarize(); print('SUMMARY',json.dumps(summary))
    if args.json:
        args.json.parent.mkdir(parents=True,exist_ok=True)
        args.json.write_text(json.dumps({'summary':summary,'checks':rep.items,'native':native['status']},indent=2))
    return 1 if rep.has_fail() else (77 if native['status']=='SKIP' else 0)


if __name__ == '__main__':
    sys.exit(main())
