#!/usr/bin/env python3
"""Offline stream-stall-guard gates. 0=native+static pass, 77=native SKIP, 1=failure.
Requires tree_sitter + tree_sitter_swift; never installs or touches a device.

1.15 replaced the old `StreamStallGuard.wrap` helper with a per-stream
`StreamStallWatchdog` plus one ticking task in processStreamEvents().  The
native half of this gate therefore runs the shipping watchdog suite that lives
next to the source it proves (`StreamStallWatchdogTests.swift`); the Python half
pins the source shape and the boundaries the reviewer must not lose:

  * one iterator pump, one watchdog tick task per stream;
  * the racer waits on the watchdog, never on a per-event sleep;
  * beginWait/endWait bracket each next();
  * fire() surfaces the retryable LLMError and the tick is cancelled on exit;
  * the cancellation/fire hand-off stays lock-protected.

--json writes a report and adjacent .artifacts production-byte package.
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
BASE = "5cceb11c020a729d6671ede52d38da174869e7d1"
SSE = "src/ios/Agent/Chat/AIChatViewModel+SSEStream.swift"
TEST = "src/ios/MinisTests/Standalone/StreamStallWatchdogTests.swift"


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
    return s, e


def parsed(kind, raw):
    """Every extracted slice must be parse-valid Swift; statements get a scope."""
    if kind in ("source", "watchdog", "box"):
        sf = Swift(raw)
    elif kind == "tick":
        sf = Swift(b"func __slice() {\n" + raw + b"\n}")
    elif kind == "reception":
        sf = Swift(b"func __slice() async throws {\n" + raw + b"\n}")
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
    marks = {
        "watchdog": (b"private final class StreamStallWatchdog",
                     b"// MARK: - Background SSE Stream Processing"),
        "box": (b"private final class StreamIteratorBox",
                b"/// [T-stream-watchdog-single-timer]"),
        "tick": (b"let stallTick = Task {", b"defer { stallTick.cancel() }"),
        "reception": (b"stallWatchdog.beginWait(at: Date())", b"stallWatchdog.endWait()"),
    }
    units, manifest = {"source": source}, {}
    manifest["source"] = {"file": SSE, "start_byte": 0, "end_byte": len(source),
                          "sha256": sha(source), "source_sha256": sha(source),
                          "bytes": len(source)}
    for kind, (start, end) in marks.items():
        s, e = slice_between(source, start, end)
        raw = source[s:e]
        parsed(kind, raw)
        units[kind] = raw
        manifest[kind] = {"file": SSE, "start_byte": s, "end_byte": e,
                          "sha256": sha(raw), "source_sha256": sha(source),
                          "bytes": len(raw)}
    return units, manifest, source


def contracts(units):
    wc = Swift(units["watchdog"]).code()
    bc = Swift(units["box"]).code()
    tc = Swift(units["tick"]).code()
    rc = Swift(units["reception"]).code()
    sc = Swift(units["source"]).code()
    return {
        # One pump: the box owns the only makeAsyncIterator()/next() pair.
        "guard.single_pump_iter": (bc.count("iterator.next()") == 1
                                   and bc.count("makeAsyncIterator()") == 1),
        # One timer per stream, one second tick; no per-event sleep remains.
        "guard.one_tick_task": sc.count("letstallTick=Task{") == 1,
        "guard.one_watchdog_sleep": tc.count("Task.sleep") == 1 and "1_000_000_000" in tc,
        "guard.tick_cancelled": "defer{stallTick.cancel()}" in tc,
        "source.no_per_event_sleep": "Task.sleep(nanoseconds:UInt64(stallTimeoutSeconds" not in sc,
        # The racer waits on the watchdog continuation, and the marks bracket next().
        "guard.wait_no_timer": "tryawaitstallWatchdog.wait()" in rc and "Task.sleep" not in rc,
        "guard.wait_marks": (rc.index("beginWait(at:Date())")
                             < rc.index("withThrowingTaskGroup")
                             < rc.index("endWait()")),
        "guard.single_consumer": (rc.count("iterBox.next()") == 1
                                  and "group.cancelAll()" in rc),
        # The timeout path fires the retryable LLMError and returns.
        "guard.fire_on_timeout": "guardmeasured>=stallTimeoutSecondselse{continue}" in tc,
        "guard.stall_is_retryable": "stallWatchdog.fire(LLMError.transientError(message:" in tc,
        "reception.stall_message": b"seconds \xe2\x80\x94 the stream stalled mid-generation" in units["tick"],
        "reception.stall_log_fields": all(s in tc for s in (
            "[StreamDiag][STALL]watchdogfired", "nominal=", "measured=", "appState=",
            "iterStart=", "firedAt=", "Self.diagAppStateName()")),
        # Fire/cancel/continuation share one lock and the waiter is resumable
        # even when cancellation lands before the continuation is stored.
        "guard.locked_state": all(s in wc for s in (
            "privateletlock=NSLock()", "withTaskCancellationHandler",
            "withCheckedThrowingContinuation", "onCancel:", "firedError",
            "resume(throwing:error)")),
        "watchdog.private": units["watchdog"].startswith(b"private final class StreamStallWatchdog"),
        "source.no_legacy_guard": "StreamStallGuard" not in sc,
    }


def native(fixture_path, work, runner):
    work.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    for key, dirname in (("HOME", "home"), ("TMPDIR", "tmp")):
        (work / dirname).mkdir(exist_ok=True)
        env[key] = str(work / dirname)
    sdk = subprocess.check_output(["xcrun", "--sdk", "macosx", "--show-sdk-path"], text=True, timeout=30).strip()
    if not sdk or not Path(sdk).is_dir():
        raise ValueError("xcrun returned no valid macOS SDK")
    exe = work / "stream-stall-watchdog-tests"
    # The suite starts with a interpreter shebang.  Strip it in a sibling copy
    # so #filePath keeps the same depth (three parents up is src/ios) and the
    # suite can still locate AIChatViewModel+SSEStream.swift.
    compile_path = fixture_path
    sibling = None
    raw = fixture_path.read_bytes()
    if raw.startswith(b"#!"):
        sibling = fixture_path.with_name(".gate-" + fixture_path.name)
        sibling.write_bytes(raw.split(b"\n", 1)[1])
        compile_path = sibling
    command = [runner, "-swift-version", "5", "-sdk", sdk,
               "-target", platform.machine() + "-apple-macosx13.0",
               "-module-cache-path", str(work / "ModuleCache"),
               str(compile_path), "-o", str(exe)]
    try:
        comp = subprocess.run(command, capture_output=True, text=True, timeout=900, env=env, cwd=work)
    finally:
        if sibling is not None:
            try:
                sibling.unlink()
            except OSError:
                pass
    (work / "compile.txt").write_text(comp.stdout + comp.stderr)
    if comp.returncode != 0 or not exe.is_file():
        raise ValueError("watchdog suite compile failure is NOT a semantic rejection; see compile.txt")
    proc = subprocess.run([str(exe)], capture_output=True, text=True, timeout=180, env=env, cwd=work)
    (work / "suite.stdout.txt").write_text(proc.stdout)
    (work / "suite.stderr.txt").write_text(proc.stderr)
    return {"command": command, "returncode": proc.returncode,
            "passed": proc.returncode == 0 and "ALL PASSED" in proc.stdout,
            "stdout_tail": proc.stdout[-4000:], "stderr_tail": proc.stderr[-2000:]}


def mutants(units):
    """Each mutant edits an extracted production slice, never a hand-written body."""
    out = []
    def add(name, kind, raw, gate):
        out.append((name, kind, raw, gate))
    w, b, t, r = (units[k] for k in ("watchdog", "box", "tick", "reception"))
    add("watchdog.fire_never", "tick",
        t.replace(b">= stallTimeoutSeconds else { continue }",
                  b">= stallTimeoutSeconds * 1000 else { continue }", 1),
        "guard.fire_on_timeout")
    add("watchdog.sleep_dropped", "tick",
        t.replace(b"try? await Task.sleep(nanoseconds: 1_000_000_000)",
                  b"try? await Task.yield()", 1),
        "guard.one_watchdog_sleep")
    add("watchdog.lock_dropped", "watchdog",
        w.replace(b"private let lock = NSLock()", b"private let lock = NSRecursiveLock()", 1),
        "guard.locked_state")
    add("watchdog.private_dropped", "watchdog",
        w.replace(b"private final class StreamStallWatchdog", b"final class StreamStallWatchdog", 1),
        "watchdog.private")
    add("tick.cancel_dropped", "tick",
        t.replace(b"defer { stallTick.cancel() }", b"defer { }", 1),
        "guard.tick_cancelled")
    add("tick.stall_message_rewritten", "tick",
        t.replace(b"seconds \xe2\x80\x94 the stream stalled mid-generation",
                  b"seconds \xe2\x80\x94 the stream STALLED mid-generation", 1),
        "reception.stall_message")
    add("reception.per_event_sleep", "reception",
        r.replace(b"group.addTask { try await iterBox.next() }",
                  b"try await Task.sleep(nanoseconds: UInt64(stallTimeoutSeconds * 1_000_000_000))\n"
                  b"                group.addTask { try await iterBox.next() }", 1),
        "guard.wait_no_timer")
    add("reception.wait_removed", "reception",
        r.replace(b"try await stallWatchdog.wait()", b"return nil", 1),
        "guard.wait_no_timer")
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

    report = {'schema_version': 2, 'base': BASE, 'source_root': str(args.source_root.resolve()),
              'limits': ['No full App build, UIKit or iOS 15 execution.',
                         'Native half runs the shipping watchdog suite; the reception loop, '
                         '[StreamDiag] fields and the retryable error type are static contracts.'],
              'items': items}
    artifact = None
    try:
        units, manifest, _ = extract(args.source_root)
        report['extraction'] = manifest
        fixture_path = (args.source_root / TEST).resolve()
        fixture = fixture_path.read_bytes()
        if not Swift(fixture).clean():
            raise ValueError('watchdog suite fixture contains syntax ERROR/missing nodes')
        report['fixture_sha256'] = sha(fixture)
        add('extract', 'production_units', 'PASS',
            '5 exact byte slices; source and slice hashes recorded')
        add('syntax', 'fixture', 'PASS', 'tree-sitter only, NOT swiftc type-check')
        current = contracts(units)
        for name, passed in current.items():
            add('static', name, 'PASS' if passed else 'FAIL')
        if args.json:
            artifact = args.json.with_suffix('.artifacts')
            artifact.mkdir(parents=True, exist_ok=True)
            for kind, raw in units.items():
                (artifact / (kind + '.swift')).write_bytes(raw)
            (artifact / 'StreamStallWatchdogTests.swift').write_bytes(fixture)
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
                result = native(fixture_path, work / 'native-baseline', runner)
                report['native'] = result
                add('native', 'shipping_watchdog_suite', 'PASS' if result['passed'] else 'FAIL',
                    'upstream StreamStallWatchdogTests.swift' if result['passed']
                    else 'rc=%d stdout=%r stderr=%r' % (result['returncode'],
                                                        result['stdout_tail'], result['stderr_tail']))
            elif not can_native:
                report['native'] = {'status': 'SKIP', 'reason': skip_reason}
                add('native', 'shipping_watchdog_suite', 'SKIP', skip_reason)
            else:
                add('native', 'shipping_watchdog_suite', 'SKIP',
                    'source contracts failed; do not compile unapproved bytes')
            variant_results = []
            for name, kind, raw, gate in mutants(units):
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
