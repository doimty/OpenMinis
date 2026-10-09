#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Standalone regression runner for [T-search-snippet-index].

Defect (device-captured): `ChatStore.keywordSnippet` found the keyword range in
`lower = text.lowercased()` and then measured it with
`text.distance(from: text.startIndex, to: range.lowerBound)` — an index owned
by a different string. Once case folding changes lengths (the Turkish "İ"
U+0130 expands to "i" + U+0307) the walk outruns `text.endIndex` and
libswiftCore raises "String index is out of bounds".

Device evidence: shared-8EEAC71D_Minis-2026-10-09-195808.ips
  sha256 7d6b466ae7aadfb490065bea3a3e46049568c5eee4df2589b2373dbe10005676
  (iPhone14,3 / iOS 15.1.1, EXC_CRASH / SIGTRAP; trap stack:
  String.index(after:) <- String.distance(from:to:) <- ChatStore.keywordSnippet
  <- ChatStore.searchMessages <- SessionsOffloadBridge.searchMessages).

Evidence classes (each check prints its class):
  [extract]  byte-exact slice of keywordSnippet() out of ChatStore.swift
             (anchored boundary, sha256 + byte count recorded; exactly one
             documented token transform `private static` -> `static` is used
             when wrapping the slice for cross-file test access)
  [native]   REAL execution: swiftc -parse-as-library <slice+wrapper> <tests>
             then run. Missing swiftc => SKIP (exit 77 overall), never a pass.
  [static]   text-level checks over the extracted slice (structure, not
             execution — never claimed as "production ran")
  [mutant]   source mutations applied in a throwaway copy; each must be
             rejected by a named static check; the historic defect mutant is
             additionally corroborated natively when a toolchain exists

Exit codes:
  0  all executed checks passed and the native suite ran
  77 static-only: every executed check passed, but the native suite was
     SKIPped because no Swift toolchain is available (NOT a native pass)
  1  at least one executed check failed

Usage:
  python3 scripts/test_keyword_snippet.py
  python3 scripts/test_keyword_snippet.py --source-root /tmp/mirror \
      --runner /usr/bin/swiftc --json out.json
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

CSTORE_REL = "src/ios/Agent/Chat/ChatStore.swift"
TEST_REL = "tests/Standalone/KeywordSnippetTests.swift"

SLICE_START = "    /// Extract a snippet around the first keyword match, up to maxLength characters."
SLICE_END = "    /// Load a page of messages for a session, ordered by sort_order ASC."

FUNCTION_HEADER = "    private static func keywordSnippet(from text: String, keywords: [String], maxLength: Int) -> String {"
FUNCTION_HEADER_TEST = "    static func keywordSnippet(from text: String, keywords: [String], maxLength: Int) -> String {"
FIXED_MATCH_LINE = "if let range = text.range(of: kw, options: [.caseInsensitive]) {"
FIXED_POS_LINE = "let pos = text.distance(from: text.startIndex, to: range.lowerBound)"

EXPECTED_NATIVE_PASS = 10


def sha256_hex(data):
    return hashlib.sha256(data).hexdigest()


class Report:
    def __init__(self):
        self.items = []

    def add(self, cls, name, status, detail=""):
        self.items.append({"cls": cls, "name": name, "status": status, "detail": detail})
        return status

    def has_fail(self):
        return any(i["status"] == "FAIL" for i in self.items)

    def summarize(self):
        counts = {}
        for i in self.items:
            counts[i["status"]] = counts.get(i["status"], 0) + 1
        return counts


# ---------------------------------------------------------------------------
# [extract] byte-exact production slice
# ---------------------------------------------------------------------------

def extract_slice(raw):
    """Slice from the doc comment above keywordSnippet() up to the next
    declaration's doc comment. Returns (slice_text, sha256, err)."""
    text = raw.decode("utf-8")
    if text.count(SLICE_START) != 1:
        return None, None, "start anchor not unique (%d hits)" % text.count(SLICE_START)
    if text.count(SLICE_END) != 1:
        return None, None, "end anchor not unique (%d hits)" % text.count(SLICE_END)
    s = text.find(SLICE_START)
    e = text.find(SLICE_END)
    if not 0 <= s < e:
        return None, None, "anchors out of order (s=%d e=%d)" % (s, e)
    byte_s = len(text[:s].encode("utf-8"))
    byte_e = len(text[:e].encode("utf-8"))
    slice_bytes = raw[byte_s:byte_e]
    slice_text = slice_bytes.decode("utf-8")
    return slice_text, sha256_hex(slice_bytes), None


def check_extract(rep, slice_text, sha):
    problems = []
    if slice_text.count(FUNCTION_HEADER) != 1:
        problems.append("function header count=%d" % slice_text.count(FUNCTION_HEADER))
    if slice_text.count("private") != 1:
        problems.append("`private` occurrences=%d (expected exactly the function token)"
                        % slice_text.count("private"))
    if slice_text.count("{") != slice_text.count("}"):
        problems.append("unbalanced braces in slice")
    if problems:
        rep.add("extract", "extract.keyword_snippet_slice", "FAIL", "; ".join(problems))
        return False
    rep.add("extract", "extract.keyword_snippet_slice", "PASS",
            "sha256=%s bytes=%d lines=%d" % (sha, len(slice_text.encode("utf-8")),
                                             slice_text.count("\n") + 1))
    return True


# ---------------------------------------------------------------------------
# [static] slice-level structure checks (not execution evidence)
# ---------------------------------------------------------------------------

def check_static(rep, slice_text):
    sub = []
    if slice_text.count(FIXED_MATCH_LINE) != 1:
        sub.append("expected exactly one case-insensitive search line")
    if sub:
        rep.add("static", "source.search_uses_case_insensitive_in_place", "FAIL", "; ".join(sub))
    else:
        rep.add("static", "source.search_uses_case_insensitive_in_place", "PASS", "")

    sub = []
    for forbidden in ("let lower = ", "lower.range(", "lower.distance(", "kw.lowercased()"):
        if forbidden in slice_text:
            sub.append("found %r" % forbidden)
    if sub:
        rep.add("static", "source.no_folded_copy_remains", "FAIL", "; ".join(sub))
    else:
        rep.add("static", "source.no_folded_copy_remains", "PASS", "")

    sub = []
    if slice_text.count(FIXED_POS_LINE) != 1:
        sub.append("expected exactly one same-string position line")
    if "range.upperBound" in slice_text:
        sub.append("range.upperBound present")
    if sub:
        rep.add("static", "source.match_pos_in_source_space", "FAIL", "; ".join(sub))
    else:
        rep.add("static", "source.match_pos_in_source_space", "PASS", "")


def wrap_production(slice_text, sha):
    transformed = slice_text.replace(FUNCTION_HEADER, FUNCTION_HEADER_TEST, 1)
    if transformed == slice_text:
        raise RuntimeError("private->internal transform anchor missing")
    return (
        "// GENERATED by scripts/test_keyword_snippet.py — do not edit.\n"
        "// Byte-exact slice of ChatStore.keywordSnippet() from\n"
        "// src/ios/Agent/Chat/ChatStore.swift (sha256 %s, %d bytes).\n"
        "// Single documented token transform: `private static` -> `static` so the\n"
        "// standalone test file can call the REAL production bytes.\n"
        "import Foundation\n\nfinal class ChatStore {\n%s}\n"
        % (sha, len(slice_text.encode("utf-8")), transformed)
    )


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


def _native_exec(runner, prod_text, test_path, workdir):
    """Compile the generated slice wrapper + the standalone test, then run it.

    Returns {"compiled": bool, "compile_log": str, "rc": int, "out": str,
             "fails": set, "summary": tuple|None}.
    """
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    for sub in ("home", "tmp", "xdg"):
        (workdir / sub).mkdir(exist_ok=True)
    prod = workdir / "prod_slice.swift"
    prod.write_text(prod_text)
    exe = workdir / "ks_runner"
    if exe.exists():
        exe.unlink()
    env = os.environ.copy()
    env["HOME"] = str(workdir / "home")
    env["TMPDIR"] = str(workdir / "tmp")
    env["XDG_CONFIG_HOME"] = str(workdir / "xdg")
    env["XDG_DATA_HOME"] = str(workdir / "xdg")
    env["XDG_CACHE_HOME"] = str(workdir / "xdg")
    cmd = [runner, "-parse-as-library", "-swift-version", "5"]
    if sys.platform == "darwin":
        sdk = subprocess.check_output(["xcrun", "--sdk", "macosx", "--show-sdk-path"],
                                      text=True).strip()
        cmd += ["-target", os.uname().machine + "-apple-macosx13.0", "-sdk", sdk,
                "-module-cache-path", str(workdir / "ModuleCache")]
    cmd += [str(prod), str(test_path), "-o", str(exe)]
    try:
        comp = subprocess.run(cmd, capture_output=True, text=True, timeout=900,
                              env=env, cwd=str(workdir))
    except subprocess.TimeoutExpired:
        return {"compiled": False, "compile_log": "swiftc timed out after 900s"}
    if comp.returncode != 0 or not exe.exists():
        return {"compiled": False, "compile_log": comp.stderr}
    try:
        proc = subprocess.run([str(exe)], capture_output=True, text=True, timeout=300,
                              env=env, cwd=str(workdir))
    except subprocess.TimeoutExpired:
        return {"compiled": True, "compile_log": "", "rc": None, "out": "runner timed out after 300s",
                "fails": set(), "summary": None}
    out = proc.stdout + ("\n[stderr]\n" + proc.stderr if proc.stderr.strip() else "")
    (workdir / "runner.stdout.txt").write_text(out)
    fails = set(re.findall(r"^FAIL\[([^\]]+)\]", proc.stdout, re.M))
    m = re.search(r"SUMMARY pass=(\d+) fail=(\d+) skip=(\d+)", proc.stdout)
    summary = tuple(int(x) for x in m.groups()) if m else None
    return {"compiled": True, "compile_log": "", "rc": proc.returncode,
            "out": out, "fails": fails, "summary": summary}


def run_main_suite(rep, runner, prod_text, test_path, workdir):
    if runner is None:
        rep.add("native", "native.keyword_snippet_suite", "SKIP",
                "no swiftc on PATH (install Xcode CLT / pass --runner); static-only evidence")
        return "SKIP"
    if not Path(test_path).exists():
        rep.add("native", "native.keyword_snippet_suite", "FAIL",
                "test file not found: %s" % test_path)
        return "FAIL"
    r = _native_exec(runner, prod_text, test_path, workdir)
    if not r["compiled"]:
        tail = "\n".join(r["compile_log"].strip().splitlines()[-12:])
        rep.add("native", "native.keyword_snippet_suite", "FAIL",
                "COMPILE failed (this is not a semantic rejection):\n%s" % tail)
        return "FAIL"
    if r["summary"] is None:
        tail = "\n".join(r["out"].strip().splitlines()[-8:])
        rep.add("native", "native.keyword_snippet_suite", "FAIL",
                "no SUMMARY line (runner rc=%s); tail:\n%s" % (r["rc"], tail))
        return "FAIL"
    p, f, k = r["summary"]
    detail = "exit=%s pass=%d fail=%d skip=%d" % (r["rc"], p, f, k)
    if r["rc"] == 0 and f == 0 and p == EXPECTED_NATIVE_PASS:
        rep.add("native", "native.keyword_snippet_suite", "PASS", detail)
        return "PASS"
    first_fails = [ln for ln in r["out"].splitlines() if ln.startswith("FAIL[")][:4]
    if p != EXPECTED_NATIVE_PASS:
        detail += "; expected pass=%d" % EXPECTED_NATIVE_PASS
    rep.add("native", "native.keyword_snippet_suite", "FAIL",
            detail + ("; " + " | ".join(first_fails) if first_fails else ""))
    return "FAIL"


# ---------------------------------------------------------------------------
# [mutant] throwaway-copy mutations; each must be rejected by a named check
# ---------------------------------------------------------------------------

TURKISH_IDS = {"turkish_dotted_i_traps_old_defect", "turkish_mixed_sentence",
               "turkish_40_expansions_past_end"}

MUTATIONS = [
    # (label, old, new, expected static rejection names, native corroboration?)
    ("folded-copy indexing reintroduced",
     "        var earliest = text.count\n"
     "        for kw in keywords {\n"
     "            if let range = text.range(of: kw, options: [.caseInsensitive]) {\n",
     "        let lower = text.lowercased()\n"
     "        var earliest = text.count\n"
     "        for kw in keywords {\n"
     "            if let range = lower.range(of: kw.lowercased()) {\n",
     {"source.no_folded_copy_remains"}, True),
    ("case-sensitive match",
     "text.range(of: kw, options: [.caseInsensitive])",
     "text.range(of: kw, options: [])",
     {"source.search_uses_case_insensitive_in_place"}, False),
    ("position measured at match end",
     FIXED_POS_LINE,
     FIXED_POS_LINE.replace("range.lowerBound", "range.upperBound"),
     {"source.match_pos_in_source_space"}, False),
]


def run_mutants(rep, source_root, runner, tmp_root):
    src = Path(source_root) / CSTORE_REL
    for idx, (label, old, new, expected_catch, want_native) in enumerate(MUTATIONS):
        mdir = Path(tmp_root) / ("m%d" % idx)
        mtarget = mdir / CSTORE_REL
        mtarget.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, mtarget)
        original = mtarget.read_text()
        if original.count(old) != 1:
            rep.add("mutant", label, "FAIL",
                    "mutation anchor is not unique (%d matches)" % original.count(old))
            continue
        mtarget.write_text(original.replace(old, new, 1))
        mslice, msha, merr = extract_slice(mtarget.read_bytes())
        if mslice is None:
            rep.add("mutant", label, "FAIL", "INVALID: extraction broke: %s" % merr)
            continue
        rr = Report()
        check_static(rr, mslice)
        caught = sorted({i["name"] for i in rr.items if i["status"] == "FAIL"}
                        & (expected_catch | {"source.search_uses_case_insensitive_in_place",
                                             "source.no_folded_copy_remains",
                                             "source.match_pos_in_source_space"}))
        missing = sorted(expected_catch - set(caught))
        if caught and not missing:
            rep.add("mutant", label, "PASS",
                    "parse-valid source rejected by " + ",".join(caught))
        else:
            rep.add("mutant", label, "FAIL",
                    "not rejected by the expected named check: missing "
                    + ",".join(missing) if missing else "no named check failed")
        if want_native:
            cname = label + " (native corroboration)"
            if runner is None:
                rep.add("mutant", cname, "SKIP",
                        "no swiftc; primary rejection is the [static] named check")
                continue
            wrap = wrap_production(mslice, msha)
            r = _native_exec(runner, wrap, Path(source_root) / TEST_REL,
                             Path(tmp_root) / ("m%d-native" % idx))
            if not r["compiled"]:
                tail = "\n".join(r["compile_log"].strip().splitlines()[-8:])
                rep.add("mutant", cname, "FAIL", "mutant failed to compile (INVALID):\n%s" % tail)
                continue
            trap_mark = "String index is out of bounds" in r["out"] or "Fatal error" in r["out"]
            if r["summary"] is None:
                rep.add("mutant", cname, "PASS",
                        "reproduced: runner terminated before SUMMARY (rc=%s)%s"
                        % (r["rc"], "; 'String index is out of bounds' seen" if trap_mark else ""))
            elif r["fails"] & TURKISH_IDS:
                hit = sorted(r["fails"] & TURKISH_IDS)[0]
                rep.add("mutant", cname, "PASS", "reproduced: FAIL[%s]" % hit)
            elif r["rc"] == 0:
                rep.add("mutant", cname, "SKIP",
                        "host toolchain does not reproduce the iOS15 trap; "
                        "rejection rests on the [static] named check")
            else:
                rep.add("mutant", cname, "PASS",
                        "rejected: exit=%s fails=%s" % (r["rc"], sorted(r["fails"])))


# ---------------------------------------------------------------------------
# entry
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source-root", type=Path, default=Path(__file__).resolve().parents[1])
    ap.add_argument("--runner")
    ap.add_argument("--json", type=Path)
    ap.add_argument("--mutants", choices=["none", "static"], default="static")
    args = ap.parse_args()

    rep = Report()
    src = Path(args.source_root) / CSTORE_REL
    if not src.exists():
        rep.add("extract", "extract.keyword_snippet_slice", "FAIL", "missing %s" % src)
        slice_text = None
    else:
        slice_text, sha, err = extract_slice(src.read_bytes())
        if slice_text is None:
            rep.add("extract", "extract.keyword_snippet_slice", "FAIL", err)
        else:
            check_extract(rep, slice_text, sha)
            check_static(rep, slice_text)

    runner, rerr = resolve_runner(args.runner)
    if rerr:
        rep.add("native", "native.toolchain", "FAIL", rerr)
    native_status = "SKIP"
    tmp = tempfile.mkdtemp(prefix="ks-snippet-")
    try:
        if slice_text is not None:
            wrap = wrap_production(slice_text, sha)
            native_status = run_main_suite(rep, runner, wrap,
                                           Path(args.source_root) / TEST_REL,
                                           Path(tmp) / "native")
            if args.mutants == "static":
                run_mutants(rep, args.source_root, runner, tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    for item in rep.items:
        print("[{cls}] {status} {name}: {detail}".format(**item))
    summary = rep.summarize()
    print("SUMMARY", json.dumps(summary))
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps({
            "summary": summary, "checks": rep.items, "native": native_status,
            "slice_sha256": sha if slice_text is not None else None,
        }, indent=2))
    if rep.has_fail():
        return 1
    return 77 if native_status == "SKIP" else 0


if __name__ == "__main__":
    sys.exit(main())
