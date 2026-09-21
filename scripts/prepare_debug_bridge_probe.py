#!/usr/bin/env python3
"""Prepare baseline/candidate bridge sources without editing production files."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess

DEFAULT_BASELINE = '3773812539d0090d2ea4c7b175d4d2a1df3bbee9'
SWIFT_SOURCES = ('src/ios/Debug/DebugLocalDispatch.swift', 'src/ios/Debug/MinisDebugLogReader.swift')
OFFLOAD_SOURCE = 'src/ios/NativeOffloads/DebugOffload.m'
PROBE_FILES = ('ProbeApp.swift', 'ProbeStubs.swift', 'BridgeProbe.h')
PROBE_INFRA = ('scripts/prepare_debug_bridge_probe.py', 'scripts/run_debug_bridge_probe.sh',
               'scripts/test_debug_bridge_names.py', '.github/workflows/ios15-debug-bridge-probe.yml')
INPUT_PATHS = SWIFT_SOURCES + (OFFLOAD_SOURCE,) + tuple('scripts/ios15-debug-bridge-probe/'+name for name in PROBE_FILES) + PROBE_INFRA


def dispatcher_slice(source):
    start = 'static NSString *dispatch_local_rpc(NSString *envelopeJSON) {'
    end = '#pragma mark - JSON-RPC invocation'
    if source.count(start) != 1 or source.count(end) != 1:
        raise ValueError('dispatcher source boundary drift')
    piece = source[source.index(start):source.index(end)].strip()
    if not piece.endswith('}') or piece.count(start) != 1:
        raise ValueError('invalid dispatcher source region')
    return piece


def validate_report(report, nonce, variant):
    if report.get('runID') != nonce or report.get('variant') != variant or report.get('os') != '26.2':
        raise ValueError('stale/wrong native report identity')
    controls = ('typedQualifiedControls', 'selectorControls', 'mainThreadGuard')
    if any(report.get(k) is not True for k in controls) or report.get('backgroundWasMain') is not False or report.get('controlsPassed') is not True:
        raise ValueError('invalid native controls')
    checks = ('coldDispatcherLookup', 'coldLogReaderLookup', 'warmDispatcherIdentity', 'warmLogReaderIdentity', 'backgroundDispatcherReachedRPC')
    if any(type(report.get(k)) is not bool for k in checks):
        raise ValueError('invalid native check types')
    if report.get('passed') is not all(report[k] for k in checks):
        raise ValueError('verdict contradicts native checks')
    if variant == 'baseline':
        if report['passed'] or any(report[k] for k in checks) or 'DebugLocalDispatch unavailable' not in str(report.get('backgroundError', '')):
            raise ValueError('baseline did not reproduce the expected two-class lookup failure')
        if report.get('runtimeDispatcherName') != 'Minis.DebugLocalDispatch' or report.get('runtimeLogReaderName') != 'Minis.MinisDebugLogReader':
            raise ValueError('baseline runtime names do not match the namespaced defect')
    elif variant == 'candidate':
        if not report['passed']:
            raise ValueError('candidate bridge is still broken')
        if report['runtimeDispatcherName'] != 'DebugLocalDispatch' or report['runtimeLogReaderName'] != 'MinisDebugLogReader':
            raise ValueError('candidate ObjC runtime names are not stable')
    else:
        raise ValueError('unknown variant')
    return report


def prepare(root, output, baseline):
    root, output = Path(root).resolve(), Path(output).resolve()
    if not re.fullmatch('[0-9a-f]{40}', baseline):
        raise ValueError('baseline must be a full SHA')
    try:
        output.relative_to(root/'src')
    except ValueError:
        pass
    else:
        raise ValueError('output cannot modify production src')
    output.mkdir(parents=True, exist_ok=True)
    candidate_head = subprocess.check_output(['git','-C',str(root),'rev-parse','HEAD'], text=True).strip()
    dirty = subprocess.check_output(['git','-C',str(root),'status','--porcelain=v1','--untracked-files=all','--',*INPUT_PATHS],text=True).splitlines()
    input_hashes = {path: hashlib.sha256((root/path).read_bytes()).hexdigest() for path in INPUT_PATHS}
    metadata = {'baseline_commit': baseline, 'candidate_head_commit': candidate_head,
                'candidate_commit': None if dirty else candidate_head,
                'candidate_source_kind': 'worktree' if dirty else 'commit',
                'candidate_dirty_inputs': dirty,
                'candidate_input_sha256': hashlib.sha256(json.dumps(input_hashes,sort_keys=True,separators=(',',':')).encode()).hexdigest(),
                'candidate_input_hashes': input_hashes, 'variants': {}, 'probe_sha256': {}}
    for name in PROBE_FILES:
        metadata['probe_sha256'][name] = input_hashes['scripts/ios15-debug-bridge-probe/'+name]
    for variant in ('baseline','candidate'):
        target=output/variant; target.mkdir(exist_ok=True)
        inputs={}
        def read_source(path):
            data = subprocess.check_output(['git','-C',str(root),'show',f'{baseline}:{path}']) if variant=='baseline' else (root/path).read_bytes()
            inputs[path] = hashlib.sha256(data).hexdigest()
            return data
        for source in SWIFT_SOURCES:
            (target/Path(source).name).write_bytes(read_source(source))
        c_source=read_source(OFFLOAD_SOURCE).decode()
        piece=dispatcher_slice(c_source)
        generated='#import "BridgeProbe.h"\n\n'+piece+'\n\nNSString *ProbeCallLocalDispatcher(NSString *envelope) { return dispatch_local_rpc(envelope); }\n'
        (target/'ProbeDispatcher.m').write_text(generated)
        metadata['variants'][variant] = {'source_sha256':inputs,'dispatcher_body_sha256':hashlib.sha256(piece.encode()).hexdigest()}
    if metadata['variants']['baseline']['dispatcher_body_sha256'] != metadata['variants']['candidate']['dispatcher_body_sha256']:
        raise ValueError('native C dispatcher changed; no longer a names-only comparison')
    (output/'inputs.json').write_text(json.dumps(metadata,indent=2)+'\n')
    return metadata


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[1])
    p.add_argument('--output',type=Path,required=True); p.add_argument('--baseline',default=DEFAULT_BASELINE)
    args=p.parse_args(); print(json.dumps(prepare(args.root,args.output,args.baseline),indent=2))
