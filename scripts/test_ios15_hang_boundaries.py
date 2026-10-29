#!/usr/bin/env python3
"""Offline hang-boundary gates. 0=native+static pass, 77=native SKIP, 1=failure.
Requires tree_sitter + tree_sitter_swift; never installs or touches a device.
Only the extracted init/helper run, with Foundation/stub dependencies, on macOS.
UIKit/Combine/availability behavior is STATIC evidence, never simulated native.
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
BASE = "b393f824c6f3d431fbe6e642c38b83f27555960c"
REG = "src/ios/Agent/Jobs/AgentJobRegistry.swift"
LIST = "src/ios/Agent/MessageList/CollectionViewMessageListV3/CollectionViewMessageListV3.swift"
INFRA = "src/ios/Agent/MessageList/MessageListInfrastructure.swift"
UI = "src/ios/Views/Chat/AIChatView/AIChatView.swift"
TEST = "tests/Standalone/HangBoundaryTests.swift"
# Frozen BASE layout bytes: covers super, background/off-window/snapshot,
# reentrancy, finite offset correction, user-scroll and width-prewarm guards.
LAYOUT_SHA = "eb3598edddc284440c826db57af7cf4907792b45ef7bdad2598c437bc1350bb1"


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

    def unique(self, nodes):
        if len(nodes) != 1:
            raise ValueError("expected one source node, got %d" % len(nodes))
        return nodes[0]

    def owner(self, name):
        return self.unique([n for n in self.nodes("class_declaration")
                            if any(c.type == "class" for c in n.children)
                            and n.child_by_field_name("name") is not None
                            and self.text(n.child_by_field_name("name")) == name])

    def member(self, owner, kind, name=None):
        body = owner.child_by_field_name("body")
        candidates = [n for n in body.named_children if n.type == kind]
        if name:
            candidates = [n for n in candidates if any(
                self.text(c) == name for c in n.named_children
                if c.type in ("simple_identifier", "pattern"))]
        return self.unique(candidates)

    def clean(self):
        return not any(n.type == "ERROR" or n.is_missing for n in walk(self.tree.root_node))


def parsed(kind, raw):
    if kind == "ui":
        raw = b"func checkUI() { view" + raw + b" }"
    else:
        raw = b"class Slice {\n" + raw + b"\n}"
    sf = Swift(raw)
    if not sf.clean():
        raise ValueError("%s slice has ERROR/missing Swift syntax nodes" % kind)
    return sf


def extract(root):
    sources = {rel: Swift((root / rel).read_bytes()) for rel in (REG, LIST, INFRA, UI)}
    # 1.15 split CollectionViewMessageListV3 into a component directory and moved
    # the coordinator members into Coordinator/*.swift, so the list side of the
    # contract has to be read from the whole tree.
    v3_dir = (root / LIST).parent
    sources[LIST] = Swift(b"\n".join(path.read_bytes() for path in sorted(v3_dir.rglob("*.swift"))))
    r, m, a, u = (sources[p] for p in (REG, LIST, INFRA, UI))
    registry = r.owner("AgentJobRegistry")
    mods = [c for c in registry.children if c.type == "modifiers"]
    if not mods or "@MainActor" not in r.code(mods[0]):
        raise ValueError("AgentJobRegistry lost MainActor isolation")
    owner = a.owner("NoAnimationCollectionView")
    pending = m.unique([n for n in m.nodes("function_declaration")
                       if any(c.type == "simple_identifier" and m.text(c) ==
                              "flushPendingSnapshotIfNeeded" for c in n.named_children)])
    calls = []
    for n in u.nodes("call_expression"):
        head = n.children[0]
        suffix = head.child_by_field_name("suffix")
        if head.type != "navigation_expression" or suffix is None or u.text(suffix) != ".onReceive":
            continue
        args = n.children[-1].named_children[0]
        if ".sessionDidUpdate" in u.text(args):
            calls.append((suffix.start_byte, n.end_byte))
    start, end = u.unique(calls)
    selection = {
        "registry": (REG, r.member(registry, "init_declaration")),
        "pending": (LIST, pending),
        "animation": (INFRA, a.member(owner, "property_declaration", "streamingGrowAnimationEnabled")),
        "layout": (INFRA, a.member(owner, "function_declaration", "layoutSubviews")),
    }
    units, manifest = {}, {}
    for kind, (rel, node) in selection.items():
        sf = sources[rel]
        raw = sf.raw[node.start_byte:node.end_byte]
        parsed(kind, raw)
        units[kind] = raw
        manifest[kind] = {"file": rel, "start_byte": node.start_byte,
                          "end_byte": node.end_byte, "sha256": sha(raw),
                          "source_sha256": sha(sf.raw), "bytes": len(raw)}
    raw = u.raw[start:end]
    parsed("ui", raw)
    units["ui"] = raw
    manifest["ui"] = {"file": UI, "start_byte": start, "end_byte": end,
                      "sha256": sha(raw), "source_sha256": sha(u.raw), "bytes": len(raw)}
    return units, manifest, sources


def contracts(units):
    sf = {k: parsed(k, v) for k, v in units.items()}
    r = sf["registry"].code()
    p = sf["pending"]
    function = p.unique(p.nodes("function_declaration"))
    statements = function.child_by_field_name("body").named_children
    statements = next(n for n in statements if n.type == "statements")
    statements = [n for n in statements.named_children if "comment" not in n.type]
    first = p.code(statements[0])
    guard = "guardhasPendingSnapshot,letvm,!vm.isLoadingSession,!vm.streamingUIUpdatesSuspendedelse{return}"
    results = {
        # 1.15 moved the readiness guards to the call site and made the body
        # nil-safe with optional chaining, so assert those semantics rather
        # than the single-guard shape this file used to have.
        "pending.nil_vm": "vm?" in p.code(function) and "vm!" not in p.code(function),
        "pending.loading": "!vm.isLoadingSession" in p.code(),
        "pending.suspended": "!vm.streamingUIUpdatesSuspended" in p.code(),
        "pending.consume_after_readiness": "hasPendingSnapshot=false" in p.code(function)
            and p.code(function).index("hasPendingSnapshot=false")
                < p.code(function).index("applySnapshot("),
        "pending.authoritative_once": p.code(function).count("applySnapshot(") == 1,
        "animation.ios15_only": sf["animation"].code() ==
            "classSlice{varstreamingGrowAnimationEnabled:Bool={if#available(iOS16.0,*){returntrue}else{returnfalse}}()}",
        "animation.frozen_layout": sha(units["layout"]) == LAYOUT_SHA,
        "ui.receive_main": ".publisher(for:.sessionDidUpdate).receive(on:DispatchQueue.main))" in sf["ui"].code(),
        "ui.session_identity": "guardletsid=vm.sessionId,letupdatedId=note.objectas?String,updatedId==sidelse{return}" in sf["ui"].code(),
    }
    for event, method, arg, label in (
        ("sessionAgentLoopDidEnd", "sessionLoopDidEnd", "sid", "loop_end"),
        ("sessionDidUpdate", "drainIfStalled", "", "activity"),
    ):
        calls = [n for n in sf["registry"].nodes("call_expression")
                 if sf["registry"].code(n.children[0]) == "NotificationCenter.default.addObserver"
                 and "forName:.%s," % event in sf["registry"].code(n)]
        code = sf["registry"].code(calls[0]) if len(calls) == 1 else ""
        results["notification.%s.queue_nil" % label] = "object:nil,queue:nil)" in code
        task = "Task{@MainActor[weakself]inself?.%s(%s)}" % (method, arg)
        results["notification.%s.actor_task_weak" % label] = task in code
        results["notification.%s.sendable" % label] = "{@Sendable[weakself]" in code
        callback = ("{@Sendable[weakself]noteinguardletsid=note.objectas?Stringelse{return}" + task + "}"
                    if arg else "{@Sendable[weakself]_in" + task + "}")
        results["notification.%s.enqueue_only" % label] = code.endswith(callback)
    return results


def wrapper(units):
    init = units["registry"]
    if not init.startswith(b"private init()"):
        raise ValueError("init visibility must be exactly one private token")
    init = b"internal" + init[len(b"private"):]
    return (b"import Foundation\nimport Dispatch\n@MainActor final class AgentJobRegistry {\n"
            b"var loopEndObserver: Any?; var activityObserver: Any?\n"
            b"var recorder: BoundaryRecorder?\n" + init + b"\n"
            b"func sessionLoopDidEnd(_ sid: String) { recorder?.sessionIds.append(sid); recorder?.mainThreadFlags.append(Thread.isMainThread) }\n"
            b"func drainIfStalled() { recorder?.activityCount += 1; recorder?.mainThreadFlags.append(Thread.isMainThread) }\n"
            b"deinit { if let t = loopEndObserver { NotificationCenter.default.removeObserver(t) }; if let t = activityObserver { NotificationCenter.default.removeObserver(t) } }\n}\n"
            b"@MainActor final class BoundaryCoordinator {\nvar vm: BoundaryVM?\nvar hasPendingSnapshot = false\n"
            b"var attempts = 0; var applied: [[BoundaryMessage]] = []\n"
            b"func reset() { attempts = 0; applied = []; hasPendingSnapshot = false }\n"
            b"func flush(_ caller: String) { flushPendingSnapshotIfNeeded(caller: caller) }\n"
            b"func applySnapshot(messages: [BoundaryMessage], caller: String) { attempts += 1; guard let vm, !vm.isLoadingSession else { return }; applied.append(messages) }\n"
            + units["pending"] + b"\n}\n")


PENDING_IDS = {"pending." + s for s in (
    "nil_vm_preserved", "nil_vm_recovers", "loading_preserved", "loading_no_apply", "loading_recovers",
    "suspended_preserved", "suspended_no_apply", "processing_resume", "latest_messages", "once",
    "no_pending_no_apply", "tail_consumed", "stop_consumed", "cancel_consumed", "replay_preserved",
    "replay_latest_once", "rebind_latest")}
NATIVE_IDS = {"pending": PENDING_IDS, "weak": {"weak." + s for s in (
    "post_returned", "released_before_actor_drain", "no_callback_after_release")}}
for _case in ("loop_end", "activity"):
    NATIVE_IDS[_case] = {"notification." + _case + "." + s for s in (
        "post_returned_while_main_held", "eventually_delivered", "actor_on_main", "exactly_once")}


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


def native(units, fixture, work, runner, cases):
    work.mkdir(parents=True, exist_ok=True)
    prod = work / "production_wrapper.swift"
    prod.write_bytes(wrapper(units))
    (work / "HangBoundaryTests.swift").write_bytes(fixture)
    env = os.environ.copy()
    for key, dirname in (("HOME", "home"), ("TMPDIR", "tmp")):
        (work / dirname).mkdir(exist_ok=True)
        env[key] = str(work / dirname)
    sdk = subprocess.check_output(["xcrun", "--sdk", "macosx", "--show-sdk-path"], text=True, timeout=30).strip()
    if not sdk or not Path(sdk).is_dir():
        raise ValueError("xcrun returned no valid macOS SDK")
    exe = work / "hang-boundary"
    command = [runner, "-parse-as-library", "-swift-version", "5", "-sdk", sdk,
               "-target", platform.machine() + "-apple-macosx13.0", "-module-cache-path", str(work / "ModuleCache"),
               str(prod), str(work / "HangBoundaryTests.swift"), "-o", str(exe)]
    comp = subprocess.run(command, capture_output=True, text=True, timeout=900, env=env, cwd=work)
    (work / "compile.txt").write_text(comp.stdout + comp.stderr)
    if comp.returncode != 0 or not exe.is_file():
        raise ValueError("compile failure is NOT a semantic rejection; see compile.txt")
    results = {}
    for case in cases:
        proc = subprocess.run([str(exe), case], capture_output=True, text=True, timeout=10, env=env, cwd=work)
        (work / (case + ".stdout.txt")).write_text(proc.stdout)
        (work / (case + ".stderr.txt")).write_text(proc.stderr)
        if proc.stderr.strip():
            raise ValueError("unexpected native stderr (not a semantic rejection)")
        results[case] = sorted(protocol(case, proc.returncode, proc.stdout))
    return {"command": command, "sdk": sdk, "cases": results}


def mutants(units):
    """Each mutant edits an extracted production slice, never a hand-written body."""
    out = []
    def add(name, kind, raw, gate, case=None, fail=None):
        out.append((name, kind, raw, gate, case, fail))
    r, p, ui, a = (units[k] for k in ("registry", "pending", "ui", "animation"))
    for event, label in (("sessionAgentLoopDidEnd", "loop_end"), ("sessionDidUpdate", "activity")):
        needle = ("forName: .%s, object: nil, queue: nil" % event).encode()
        add(label + ".queue_main", "registry", r.replace(needle, needle[:-3] + b".main", 1),
            "notification." + label + ".queue_nil", label,
            "notification." + label + ".post_returned_while_main_held")
    add("notification.drop_actor", "registry", r.replace(b"@MainActor ", b"", 1),
        "notification.loop_end.actor_task_weak")
    add("notification.inline_method", "registry", re.sub(
        rb"Task \{ @MainActor \[weak self\] in (self\?\.sessionLoopDidEnd\(sid\)) \}", rb"\1", r, count=1),
        "notification.loop_end.enqueue_only")
    add("notification.strong_task", "registry", r.replace(b"@MainActor [weak self]", b"@MainActor [self]", 1),
        "notification.loop_end.actor_task_weak", "weak", "weak.released_before_actor_drain")
    add("notification.drop_sendable", "registry", r.replace(b") { @Sendable [weak self] note", b") { [weak self] note", 1),
        "notification.loop_end.sendable")
    add("ui.drop_receive", "ui", ui.replace(b".receive(on: DispatchQueue.main)", b"", 1), "ui.receive_main")
    add("pending.drop_loading", "pending", re.sub(rb"\s*!vm.isLoadingSession,", b"", p, count=1),
        "pending.loading", "pending", "pending.loading_preserved")
    add("pending.drop_suspended", "pending", re.sub(rb",\s*!vm.streamingUIUpdatesSuspended", b"", p, count=1),
        "pending.suspended", "pending", "pending.suspended_preserved")
    sf = parsed("pending", p)
    guard = sf.text(sf.unique(sf.nodes("guard_statement"))).encode()
    clear = b"hasPendingSnapshot = false"
    late = p.replace(guard, b"guard hasPendingSnapshot else { return }", 1).replace(
        clear, clear + b"\n" + guard.replace(b"hasPendingSnapshot, ", b"", 1), 1)
    add("pending.nil_vm_late", "pending", late, "pending.nil_vm", "pending", "pending.nil_vm_preserved")
    early = p.replace(clear, b"", 1).replace(guard, clear + b"\n" + guard, 1)
    add("pending.clear_early", "pending", early, "pending.consume_after_readiness", "pending", "pending.loading_preserved")
    add("pending.drop_pending", "pending", p.replace(b"hasPendingSnapshot, ", b"", 1),
        "pending.consume_after_readiness", "pending", "pending.no_pending_no_apply")
    add("pending.processing_gate", "pending", p.replace(b"!vm.streamingUIUpdatesSuspended", b"!vm.streamingUIUpdatesSuspended, !vm.isProcessing", 1),
        "pending.consume_after_readiness", "pending", "pending.processing_resume")
    add("pending.stale_messages", "pending", p.replace(b"applySnapshot(messages: vm.messages", b"applySnapshot(messages: []", 1),
        "pending.authoritative_once", "pending", "pending.latest_messages")
    for value in (b"true", b"false"):
        add("animation.all_" + value.decode(), "animation", b"var streamingGrowAnimationEnabled: Bool = " + value,
            "animation.ios15_only")
    add("animation.skip_super", "layout", units["layout"].replace(b"super.layoutSubviews()", b"setNeedsLayout()", 1),
        "animation.frozen_layout")
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
              'limits': ['No full App build, UIKit, Combine runtime, or iOS 15 execution.',
                         'Foundation tests execute extracted methods with dependency spies, not full AgentJobRegistry scheduling.',
                         'Availability and preserved layout are static source contracts.'], 'items': items}
    artifact = None
    try:
        units, manifest, _ = extract(args.source_root)
        report['extraction'] = manifest
        fixture = (args.source_root / TEST).read_bytes()
        if not Swift(fixture).clean():
            raise ValueError('Swift test fixture contains syntax ERROR/missing nodes')
        report['fixture_sha256'] = sha(fixture)
        report['visibility_transform'] = 'Registry init private -> internal only; pending helper bytes unchanged.'
        add('extract', 'production_units', 'PASS', '5 exact byte slices; source and slice hashes recorded')
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
            (artifact / 'HangBoundaryTests.swift').write_bytes(fixture)
            (artifact / 'manifest.json').write_text(json.dumps(manifest, indent=2))
        runner = args.runner or shutil.which('swiftc')
        if args.runner:
            located = shutil.which(args.runner)
            if not located:
                raise ValueError('Explicit --runner not executable: ' + args.runner)
            runner = located
        can_native = bool(runner) and platform.system() == 'Darwin'
        skip_reason = ('no swiftc available; native suite not executed' if not runner else
                       'macOS Foundation required; other platforms cannot prove main-queue behavior')
        with tempfile.TemporaryDirectory(prefix='hang-boundary-') as td:
            work = artifact if artifact is not None else Path(td)
            if can_native and all(current.values()):
                result = native(units, fixture, work / 'native-baseline', runner, list(NATIVE_IDS))
                report['native'] = result
                failures = {case: ids for case, ids in result['cases'].items() if ids}
                add('native', 'production_methods', 'FAIL' if failures else 'PASS',
                    json.dumps(failures) if failures else '28 named assertions, 4 modes, real macOS Foundation')
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
                detail.update(parse_valid=True, rejected_by=[k for k,v in result.items() if not v])
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
