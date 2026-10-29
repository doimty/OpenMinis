#!/usr/bin/env python3
"""Full-source repair-candidate overlay, separate from the immutable baseline profile."""
from __future__ import annotations
import argparse
import importlib.util
import json
from pathlib import Path
import plistlib
import re
import check_candidate_source as gate

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
PROBE = HERE.parent / 'ios15-provisional-height-probe'
spec = importlib.util.spec_from_file_location('baseline_probe_prepare', PROBE / 'prepare_device_probe.py')
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


def prepare_sources(build_root, repository, profile_path=None):
    build_root, repository = Path(build_root), Path(repository)
    source_gate = gate.verify(build_root, repository, profile_path)
    before = {name: (build_root / name).read_bytes() for name in probe.BASELINE_PATHS}
    main = before[probe.MAIN_PATH].decode()
    gate.require(main.count(probe.ENTRY) == 1, 'candidate app entrypoint must be unique')
    driver = PROBE / 'DeviceProbe.swift'
    driver_text = driver.read_text()
    gate.require(driver_text.count('@main') == 1 and 'final class NativeProvisionalHeightApp' in driver_text,
                 'missing unique replay entrypoint')
    for name in ('SelectableMarkdownTextView', 'LegacyHostingContentView', 'SelfSizingCell', 'MessageListLayout'):
        gate.require(not re.search(r'\bclass\s+' + name + r'\b', driver_text), 'driver substitutes production type: ' + name)
    generated = {
        probe.MAIN_PATH: main.replace(probe.ENTRY, probe.NO_ENTRY),
        probe.DEBUG_PATH: before[probe.DEBUG_PATH].decode() + probe.START + driver_text + '\n' + probe.END,
        probe.LOGGER_PATH: before[probe.LOGGER_PATH].decode() + probe.START + probe.READONLY_COLLECTOR + probe.END,
    }
    for name, text in generated.items():
        (build_root / name).write_text(text)
    gate.require(probe.restore_sources(build_root) == before, 'candidate overlay cannot restore exact pre-overlay bytes')
    for name in probe.RENDER_PATHS:
        gate.require((build_root / name).read_bytes() == before[name], 'overlay changed production rendering source: ' + name)
    return {
        'kind': 'full-app-device-height-repair-candidate-overlay',
        'baselineCommit': gate.BASELINE, 'buildCommit': source_gate['buildCommit'],
        'sourceGate': source_gate, 'driverSHA256': gate.sha(driver.read_bytes()),
        'sourceSHA256Before': {name: gate.sha(data) for name, data in before.items()},
        'sourceSHA256After': {name: gate.sha((build_root / name).read_bytes()) for name in probe.BASELINE_PATHS},
        'modifiedSources': list(generated), 'renderingSourcesUnchangedByOverlay': True,
        'otherProductionSourcesUnchanged': True, 'exactCandidateInverseVerified': True,
        'nativeExecution': 'NOT_RUN',
        'limits': 'One approved layout-policy production diff; unchanged real replay driver/renderers; device execution still required.',
    }


def prepare_bundle(source_app, output, commit, profile_path=None):
    profile = json.loads(Path(profile_path or HERE / 'candidate-source.json').read_text())
    gate.require(profile['baselineCommit'] == gate.BASELINE and set(profile['productionChanges']) == gate.ALLOWED,
                 'invalid candidate package profile')
    app = probe.prepare_bundle(source_app, output, commit)
    path = app / 'Info.plist'
    info = plistlib.loads(path.read_bytes())
    info.update(CFBundleName='Minis Height Fix', CFBundleDisplayName='Minis Height Fix',
                MinisHeightRepairCandidate=True,
                MinisHeightPolicySHA256=profile['productionChanges'][gate.LAYOUT]['candidateSHA256'])
    path.write_bytes(plistlib.dumps(info, fmt=plistlib.FMT_BINARY))
    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    sources = sub.add_parser('sources')
    sources.add_argument('--root', type=Path, required=True)
    sources.add_argument('--repository', type=Path, default=ROOT)
    sources.add_argument('--profile', type=Path)
    sources.add_argument('--manifest', type=Path, required=True)
    bundle = sub.add_parser('bundle')
    bundle.add_argument('--app', type=Path, required=True)
    bundle.add_argument('--output', type=Path, required=True)
    bundle.add_argument('--commit', required=True)
    bundle.add_argument('--profile', type=Path)
    args = parser.parse_args()
    if args.action == 'sources':
        result = prepare_sources(args.root, args.repository, args.profile)
        args.manifest.parent.mkdir(parents=True, exist_ok=True)
        args.manifest.write_text(json.dumps(result, indent=2) + '\n')
        print('Approved single-file candidate diff; exact replay-overlay inverse verified; device execution NOT_RUN.')
    else:
        print(prepare_bundle(args.app, args.output, args.commit, args.profile))


if __name__ == '__main__':
    main()
