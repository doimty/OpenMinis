#!/usr/bin/env python3
"""Offline stream-stall-guard gates. 0=native+static pass, 77=native SKIP, 1=failure.
Requires tree_sitter + tree_sitter_swift; never installs or touches a device.
Only the extracted StreamStallGuard unit and the reception slice are inspected;
the native suite compiles the production guard with Foundation on macOS.
UIKit/app behavior (the [StreamDiag] call sites, LLMError construction, the
retry pipeline) is STATIC evidence, never simulated native.
--json writes a versioned report and adjacent .artifacts production-byte package.
"""
import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from tree_sitter import Language, Parser
import tree_sitter_swift

PARSER = Parser(Language(tree_sitter_swift.language()))
BASE = "d296e62c566bd09317cb461f78366f43f65fb23f"
SSE = "src/ios/Agent/Chat/AIChatViewModel+SSEStream.swift"
TEST = "tests/Standalone/StreamStallGuardTests.swift"
# Frozen 2026-10-12 bytes (sha256 of each pinned production slice). The
# timeline and tail slices must stay byte-identical to BASE d296e62; the guard
# and reception slices pin the new [T-stream-stall-guard] shape.
GUARD_SHA = "a62fa132b03b473c8cde5955d3b3396805fb1c25b5fcbdeefa5329557e512386"
RECEPTION_SHA = "f1873af5d60f054dfd30cf28327b7f1bc8a3e299bf81eaecb2d2dbc3b46c605a"
TIMELINE_SHA = "273916d4e402c153a584a52c178944e46abf1153561c3fb891580c76d482510e"
TAIL_SHA = "86faf2bb1bd9bf11e773b66e9c771facc1c33eb469f9e62e32d8679ee0cd1115"


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def walk(node):
    yield node
    for child in node.children:
        yield from walk(child)


class Swift:
    def __init__(self, raw):
        self.raw = raw
        self.tree = PARSER.parse(raw)

    def text(self, node):
        return self.raw[node.start_byte:node.end_byte].decode()

    def nodes(self, kind, within=None):
        return [n for n in walk(within or self.tree.root_node) if n.type == kind]

    def code(self, node=None):
        def leaves(n):
            if "comment" in n.type:
                return ""
            if not n.children:
                return self.text(n)
            return "".join(leaves(c) for c in n.children)
        return re.sub(r"\s+", "", leaves(node or self.tree.root_node))

    def clean(self):
        return not any(n.type == "ERROR" or n.is_missing for n in walk(self.tree.root_node))


def slice_between(raw, start, end):
    s = raw.index(start)
    e = raw.index(end, s) + len(end)
    return raw[s:e]


def parsed(kind, raw):
    """Each extracted slice must be parse-valid Swift; fragments get balanced braces."""
    if kind == "guard":
        sf = Swift(raw)
    elif kind == "reception":  # func { do { while { <slice> -> close while/do/func
        sf = Swift(b"func __slice() {\n" + raw + b"\n            }\n        }\n}")
    elif kind == "timeline":  # balanced do-block + func closer
        sf = Swift(b"func __slice() {\n" + raw + b"\n}")
    elif kind == "tail":  # leading '}' closes the injected do, catches attach to it
        sf = Swift(b"func __slice() {\n        do {\n" + raw + b"\n}")
    else:
        raise ValueError("unknown slice kind " + kind)
    if not sf.clean():
        raise ValueError("%s slice has ERROR/missing Swift syntax nodes" % kind)
    return sf


def extract(root):
    source = (root / SSE).read_bytes()
    sf = Swift(source)
    if not sf.clean():
        raise ValueError("source file has ERROR/missing Swift syntax nodes")
    guards = [n for n in sf.nodes("class_declaration")
              if source[n.start_byte:n.start_byte + 40].startswith(b"private enum StreamStallGuard")]
    if len(guards) != 1:
        raise ValueError("expected exactly one StreamStallGuard declaration, got %d" % len(guards))
    slices = {
        "guard": source[guards[0].start_byte:guards[0].end_byte],
        "reception": slice_between(
            source,
            b"// [T-stream-stall-guard] Per-stream watchdog (one pumper + one",
            b"try Task.checkCancellation()"),
        "timeline": slice_between(
            source,
            b"// [StreamDiag] Per-event timeline.",
            b"lastEventAt = now\n            }"),
        "tail": slice_between(
            source,
            b"// The AsyncThrowingStream may silently terminate",
            b"if let err = _streamError { throw err }"),
    }
    units, manifest = {}, {}
    for kind, raw in slices.items():
        parsed(kind, raw)
        units[kind] = raw
        manifest[kind] = {"file": SSE, "sha256": sha(raw), "bytes": len(raw),
                          "source_sha256": sha(source)}
    return units, manifest, source


def contracts(units):
    gc = Swift(units["guard"]).code()
    rc = Swift(units["reception"]).code()
    raw_r = units["reception"]
    result = {}
    result["guard.single_pump_iter"] = (gc.count("iterator.next()") == 1
                                        and gc.count("makeAsyncIterator()") == 1)
    result["guard.one_watchdog_sleep"] = gc.count("Task.sleep") == 1
    result["guard.unbounded_stream"] = "returnAsyncThrowingStream{contin" in gc
    result["guard.explicit_termination"] = \
        "cont.onTermination={_inpumper.cancel()watchdog.cancel()}" in gc
    result["guard.once_finish"] = (
        "ifsync.beginFinish(){cont.finish()}" in gc
        and "ifsync.beginFinish(){cont.finish(throwing:error)}" in gc
        and "ifsync.beginFinish(){leterror=awaitonStall(fire)cont.finish(throwing:error)}" in gc)
    result["guard.fire_condition"] = \
        "guardnow.timeIntervalSince(lastEventAt)>=stallTimeoutelse{returnnil}" in gc
    result["guard.counts_events"] = "lastEventAt=Date()events+=1" in gc
    result["guard.yields_events"] = "cont.yield(element)" in gc
    result["guard.locked_state"] = ("privateletlock=NSLock()" in gc
                                    and gc.count("lock.lock()") >= 4
                                    and "funcbeginFinish()->Bool" in gc)
    result["guard.no_taskgroup"] = "withThrowingTaskGroup" not in gc
    result["guard.frozen_unit"] = sha(units["guard"]) == GUARD_SHA
    result["reception.no_per_event_group"] = \
        "withThrowingTaskGroup" not in rc and "cancelAll" not in rc
    result["reception.single_consumer"] = rc.count("iterBox.next()") == 1
    result["reception.guard_wired"] = (
        "letguardedStream=StreamStallGuard.wrap(stream,stallTimeout:stallTimeoutSeconds,onStall:{firein" in rc
        and "letiterBox=StreamIteratorBox(guardedStream)" in rc)
    result["reception.recheck_cancellation"] = "tryTask.checkCancellation()" in rc
    result["reception.stall_message"] = \
        b"seconds \xe2\x80\x94 the stream stalled mid-generation" in raw_r
    result["reception.stall_log_fields"] = (
        b"[StreamDiag][STALL] watchdog fired session=" in raw_r
        and b"provider=\\(streamDiagProvider) model=\\(streamDiagModel)" in raw_r
        and b"fire.eventsThisStream" in raw_r
        and b"fire.iterStart" in raw_r
        and b"fire.firedAt" in raw_r
        and b"Self.diagAppStateName()" in raw_r)
    result["reception.frozen_slice"] = sha(units["reception"]) == RECEPTION_SHA
    result["frozen.timeline"] = sha(units["timeline"]) == TIMELINE_SHA
    result["frozen.tail"] = sha(units["tail"]) == TAIL_SHA
    return result


def wrapper(units):
    guard = units["guard"]
    if not guard.startswith(b"private enum StreamStallGuard"):
        raise ValueError("guard unit visibility prefix changed; wrapper transform unsafe")
    stripped = guard.replace(b"private enum StreamStallGuard", b"enum StreamStallGuard", 1)
    return b"import Foundation\n" + stripped + b"\n"


NATIVE_IDS = {
    "pass": {"pass.events_ordered", "pass.clean_end", "pass.no_stall"},
    "burst": {"burst.count", "burst.ordered", "burst.clean_end"},
    "stall": {"stall.first_event", "stall.error_is_stall", "stall.fired_once",
              "stall.timing", "stall.snapshot_fields", "stall.no_second_fire"},
    "upstream": {"upstream.events", "upstream.error_is_upstream", "upstream.no_stall"},
    "cancel": {"cancel.consumer_resumed", "cancel.upstream_terminated", "cancel.no_stall"},
}


def protocol(case, rc, stdout):
    """Crashes, timeouts, empty/truncated/fabricated summaries are never kills."""
    lines = stdout.strip().splitlines()
    if not lines:
        raise ValueError("empty native output")
    summary = re.fullmatch(r"SUMMARY case=(\w+) pass=(\d+) fail=(\d+) skip=(\d+)", lines[-1])
    if not summary or summary[1] != case:
        raise ValueError("missing/wrong terminal summary")
    records = [re.fullmatch(r"(PASS|FAIL)\[([^]]+)\]", line) for line in lines[:-1]]
    if not all(records):
        raise ValueError("unexpected native output line")
    ids = [r[2] for r in records]
    if len(ids) != len(set(ids)) or set(ids) != NATIVE_IDS[case]:
        raise ValueError("missing/duplicate/unknown assertion id")
    failures = {r[2] for r in records if r[1] == "FAIL"}
    p, f, k = map(int, summary.groups()[1:])
    if (p, f, k) != (len(ids) - len(failures), len(failures), 0) or rc != (1 if f else 0):
        raise ValueError("summary/count/exit mismatch (crashes are not semantic failures)")
    return failures


def case_result(case, rc, stdout):
    """JSON-safe per-case failure list. Sets are not serializable and this is a
    CI-only path (native SKIPs without swiftc), so keep the conversion here —
    the runner self-test pins it."""
    return sorted(protocol(case, rc, stdout))


def native(units, fixture, work, runner, cases):
    work.mkdir(parents=True, exist_ok=True)
    prod = work / "production_wrapper.swift"
    prod.write_bytes(wrapper(units))
    (work / "StreamStallGuardTests.swift").write_bytes(fixture)
    env = os.environ.copy()
    for key, dirname in (("HOME", "home"), ("TMPDIR", "tmp")):
        (work / dirname).mkdir(exist_ok=True)
        env[key] = str(work / dirname)
    sdk = subprocess.check_output(["xcrun", "--sdk", "macosx", "--show-sdk-path"], text=True, timeout=30).strip()
    if not sdk or not Path(sdk).is_dir():
        raise ValueError("xcrun returned no valid macOS SDK")
    exe = work / "stream-stall-guard"
    command = [runner, "-parse-as-library", "-swift-version", "5", "-sdk", sdk,
               "-target", platform.machine() + "-apple-macosx13.0", "-module-cache-path", str(work / "ModuleCache"),
               str(prod), str(work / "StreamStallGuardTests.swift"), "-o", str(exe)]
    comp = subprocess.run(command, capture_output=True, text=True, timeout=900, env=env, cwd=work)
    (work / "compile.txt").write_text(comp.stdout + comp.stderr)
    if comp.returncode != 0 or not exe.is_file():
        raise ValueError("compile failure is NOT a semantic rejection; see compile.txt")
    results = {"cases": {}}
    for case in cases:
        proc = subprocess.run([str(exe), case], capture_output=True, text=True, timeout=10, env=env, cwd=work)
        (work / (case + ".stdout.txt")).write_text(proc.stdout)
        (work / (case + ".stderr.txt")).write_text(proc.stderr)
        if proc.stderr.strip():
            raise ValueError("unexpected native stderr (not a semantic rejection)")
        results["cases"][case] = case_result(case, proc.returncode, proc.stdout)
    return results


def mutants(units):
    """Each mutant edits an extracted production slice, never a hand-written body."""
    out = []

    def add(name, kind, raw, gate, case=None, fail=None):
        out.append((name, kind, raw, gate, case, fail))

    g, r = units["guard"], units["reception"]
    add("guard.termination_dropped", "guard",
        g.replace(b"cont.onTermination = { _ in\n                pumper.cancel()\n                watchdog.cancel()\n            }",
                  b"cont.onTermination = { _ in\n            }", 1),
        "guard.explicit_termination", "cancel", "cancel.upstream_terminated")
    add("guard.never_fires", "guard",
        g.replace(b">= stallTimeout else { return nil }",
                  b">= stallTimeout * 1000 else { return nil }", 1),
        "guard.fire_condition")
    add("guard.double_finish_race", "guard",
        g.replace(b"if sync.beginFinish() {\n                        let error = await onStall(fire)",
                  b"if true {\n                        let error = await onStall(fire)", 1),
        "guard.once_finish")
    add("guard.non_plain_lock", "guard",
        g.replace(b"private let lock = NSLock()",
                  b"private let lock = NSRecursiveLock()", 1),
        "guard.locked_state")
    add("guard.events_not_counted", "guard",
        g.replace(b"events += 1", b"events += 0", 1),
        "guard.counts_events", "stall", "stall.snapshot_fields")
    add("guard.last_event_never", "guard",
        g.replace(b"lastEventAt = Date()\n            events += 1", b"events += 1", 1),
        "guard.counts_events")
    add("guard.yield_dropped", "guard",
        g.replace(b"cont.yield(element)", b"_ = element", 1),
        "guard.yields_events", "pass", "pass.events_ordered")
    add("guard.error_not_forwarded", "guard",
        g.replace(b"if sync.beginFinish() { cont.finish(throwing: error) }",
                  b"if sync.beginFinish() { cont.finish() }", 1),
        "guard.once_finish", "upstream", "upstream.error_is_upstream")
    add("reception.per_event_group_returns", "reception",
        r.replace(b"let maybeEvent: AgentStreamEvent? = try await iterBox.next()",
                  (b"let maybeEvent: AgentStreamEvent? = try await withThrowingTaskGroup(of: AgentStreamEvent?.self) { group in\n"
                   b"                group.addTask { try await iterBox.next() }\n"
                   b"                let result = try await group.next()!\n"
                   b"                group.cancelAll()\n"
                   b"                return result\n"
                   b"            }"), 1),
        "reception.no_per_event_group")
    add("reception.guard_bypassed", "reception",
        r.replace(b"let iterBox = StreamIteratorBox(guardedStream)",
                  b"let iterBox = StreamIteratorBox(stream)", 1),
        "reception.guard_wired")
    add("reception.stall_message_rewritten", "reception",
        r.replace(b"seconds \xe2\x80\x94 the stream stalled mid-generation",
                  b"seconds \xe2\x80\x94 the stream STALLED mid-generation", 1),
        "reception.stall_message")
    add("frozen.timeline_edited", "timeline",
        units["timeline"].replace(b"if eventSeq <= 5 || gap >= 5 {", b"if eventSeq <= 6 || gap >= 5 {", 1),
        "frozen.timeline")
    add("frozen.tail_edited", "tail",
        units["tail"].replace(b"_streamError = error", b"if true { _streamError = error }", 1),
        "frozen.tail")
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--json', type=Path)
    parser.add_argument('--runner', help='Explicit macOS swiftc; absent compiler is SKIP, never PASS')
    args = parser.parse_args(argv)
    items = []

    def add(cls, name, status, detail=''):
        items.append({'class': cls, 'name': name, 'status': status, 'detail': detail})
        print('[%s] %s %s: %s' % (cls, status, name, detail), flush=True)

    report = {'schema_version': 1, 'base': BASE, 'source_root': str(args.source_root.resolve()),
              'limits': ['No full App build, UIKit or iOS 15 execution.',
                         'The native suite compiles the extracted guard with Foundation; the '
                         'reception slice, [StreamDiag] call sites and LLMError wiring are static contracts.'],
              'items': items}
    artifact = None
    try:
        units, manifest, _ = extract(args.source_root)
        report['extraction'] = manifest
        fixture = (args.source_root / TEST).read_bytes()
        if not Swift(fixture).clean():
            raise ValueError('Swift test fixture contains syntax ERROR/missing nodes')
        report['fixture_sha256'] = sha(fixture)
        report['visibility_transform'] = 'Guard enum private -> internal only; slice bytes unchanged.'
        add('extract', 'production_units', 'PASS', '4 exact byte slices; source and slice hashes recorded')
        add('syntax', 'fixture', 'PASS', 'tree-sitter only, NOT swiftc type-check')
        current = contracts(units)
        for name, passed in current.items():
            add('static', name, 'PASS' if passed else 'FAIL')
        if args.json:
            artifact = args.json.with_suffix('.artifacts')
            artifact.mkdir(parents=True, exist_ok=True)
            for kind, raw in units.items():
                (artifact / (kind + '.swift')).write_bytes(raw)
            (artifact / 'production_wrapper.swift').write_bytes(wrapper(units))
            (artifact / 'StreamStallGuardTests.swift').write_bytes(fixture)
            (artifact / 'manifest.json').write_text(json.dumps(manifest, indent=2))
        runner = args.runner or shutil.which('swiftc')
        if args.runner:
            located = shutil.which(args.runner)
            if not located:
                raise ValueError('Explicit --runner not executable: ' + args.runner)
            runner = located
        can_native = bool(runner) and platform.system() == 'Darwin'
        skip_reason = ('no swiftc available; native suite not executed' if not runner else
                       'macOS Foundation required; other platforms cannot run the suite')
        with tempfile.TemporaryDirectory(prefix='stream-stall-guard-') as td:
            work = artifact if artifact is not None else Path(td)
            if can_native and all(current.values()):
                result = native(units, fixture, work / 'native-baseline', runner, list(NATIVE_IDS))
                report['native'] = result
                failures = {case: ids for case, ids in result['cases'].items() if ids}
                add('native', 'production_methods', 'FAIL' if failures else 'PASS',
                    json.dumps(failures) if failures else '18 named assertions, 5 modes, real macOS Foundation')
            elif not can_native:
                report['native'] = {'status': 'SKIP', 'reason': skip_reason}
                add('native', 'production_methods', 'SKIP', skip_reason)
            else:
                add('native', 'production_methods', 'SKIP', 'source contracts failed; do not compile unapproved bytes')
            variant_results = []
            for name, kind, raw, gate, case, expected_fail in mutants(units):
                detail = {'name': name, 'kind': kind, 'gate': gate}
                variant_results.append(detail)
                if raw == units[kind]:
                    add('mutant', name, 'FAIL', 'mutation was a no-op'); continue
                try:
                    parsed(kind, raw)
                except ValueError as exc:
                    add('mutant', name, 'FAIL', 'INVALID syntax; not a rejection: ' + str(exc)); continue
                variant = dict(units); variant[kind] = raw
                result = contracts(variant)
                rejected = not result.get(gate, True)
                detail.update(parse_valid=True, rejected_by=[k for k, v in result.items() if not v])
                add('mutant-static', name, 'PASS' if rejected else 'FAIL',
                    'parse-valid rejected by ' + gate if rejected else 'mutant survived named source gate')
                if artifact:
                    mdir = artifact / 'mutants' / name; mdir.mkdir(parents=True, exist_ok=True)
                    (mdir / (kind + '.swift')).write_bytes(raw)
                if case:
                    if can_native and all(current.values()):
                        try:
                            outcome = native(variant, fixture, work / ('native-' + name), runner, [case])
                            failures = outcome['cases'][case]
                            detail['native'] = outcome
                            add('mutant-native', name, 'PASS' if expected_fail in failures else 'FAIL',
                                'named semantic failures=' + repr(failures))
                        except (ValueError, subprocess.TimeoutExpired) as exc:
                            add('mutant-native', name, 'FAIL', 'INVALID native run, not a kill: ' + str(exc))
                    else:
                        detail['native'] = {'status': 'SKIP', 'reason': skip_reason}
                        add('mutant-native', name, 'SKIP', skip_reason)
            report['mutants'] = variant_results
    except Exception as exc:
        add('runner', 'execution', 'FAIL', type(exc).__name__ + ': ' + str(exc))
    summary = {s: sum(i['status'] == s for i in items) for s in ('PASS', 'FAIL', 'SKIP')}
    report['summary'] = summary
    if summary['FAIL']:
        rc = 1
    elif any(i['status'] == 'SKIP' for i in items):
        rc = 77
    else:
        rc = 0
    report['exit_code'] = rc
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(report, indent=2) + '\n')
    print('SUMMARY ' + json.dumps(summary), flush=True)
    return rc


if __name__ == '__main__':
    sys.exit(main())
