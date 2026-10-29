#!/usr/bin/env python3
"""Generate a test-access copy; never rewrite the production source.

Only the access level of the notification method and payload are widened in the
generated copy (plus a probe-only generation getter). No sizing, coalescing,
callback, lifecycle, or dispatch logic is changed by this script.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

RELATIVE_SOURCE = Path('src/ios/Shared/LegacyHostingContent.swift')
PRIVATE_SEAM = '    private func contentSizeChanged(_ payload: LegacyHostedSize) {'
TEST_SEAM = '    func contentSizeChanged(_ payload: LegacyHostedSize) {'
GETTER_HEAD = '\n    // === BEGIN SIZE-DELIVERY PROBE GETTER ==='
GETTER_TAIL = '    // === END SIZE-DELIVERY PROBE GETTER ===\n'
CASE_NAMES = (
    'single-sample-control', 'duplicate-sample-control', 'coalesced-shrink',
    'coalesced-growth', 'replacement-without-new-sample', 'replacement-with-new-sample',
)


def validate_native_report(report, expected_run_id):
    if report.get('runID') != expected_run_id or report.get('os') != '26.2':
        raise ValueError('report run/runtime mismatch')
    cases = report.get('cases', [])
    actual = [(case.get('name'), case.get('repetition')) for case in cases]
    expected = {(name, repetition) for name in CASE_NAMES for repetition in (1, 2, 3)}
    if len(actual) != len(expected) or len(set(actual)) != len(actual) or set(actual) != expected:
        raise ValueError('incomplete or duplicate case identity matrix')
    if any(type(case.get('passed')) is not bool for case in cases):
        raise ValueError('case verdict must be a boolean')
    controls = all(case['passed'] for case in cases if case['name'].endswith('control'))
    passed = controls and all(case['passed'] for case in cases)
    if type(report.get('controlsPassed')) is not bool or report['controlsPassed'] != controls:
        raise ValueError('control summary contradicts cases')
    if type(report.get('passed')) is not bool or report['passed'] != passed:
        raise ValueError('overall summary contradicts cases')
    return report


def testable_source(source):
    if source.count(PRIVATE_SEAM) == 1:
        source = source.replace(PRIVATE_SEAM, TEST_SEAM, 1)
    elif source.count(TEST_SEAM) != 1:
        raise ValueError('expected exactly one contentSizeChanged seam')
    # The payload type is file-private; the probe (another file in the same
    # module) must see it to construct explicit samples.
    if source.count('private struct LegacyHostedSize: Equatable') != 1:
        raise ValueError('expected exactly one private LegacyHostedSize payload')
    source = source.replace('private struct LegacyHostedSize: Equatable', 'struct LegacyHostedSize: Equatable', 1)
    getter = (GETTER_HEAD + '\n'
              '    /// Probe-only read of the current configuration generation so\n'
              '    /// explicit seam samples carry a generation the guard accepts.\n'
              '    var probeConfigurationGeneration: UInt { configurationGeneration }\n'
              + GETTER_TAIL)
    anchor = '    override func layoutSubviews() {'
    if source.count(anchor) != 1:
        raise ValueError('layoutSubviews anchor drifted')
    source = source.replace(anchor, getter + anchor, 1)
    return source


def restore_source(generated):
    if generated.count(GETTER_HEAD) != 1 or generated.count(GETTER_TAIL) != 1:
        raise ValueError('getter markers drifted')
    start = generated.index(GETTER_HEAD)
    end = generated.index(GETTER_TAIL) + len(GETTER_TAIL)
    text = generated[:start] + generated[end:]
    if text.count('struct LegacyHostedSize: Equatable') != 1:
        raise ValueError('payload visibility marker drifted')
    text = text.replace('struct LegacyHostedSize: Equatable', 'private struct LegacyHostedSize: Equatable', 1)
    if text.count(TEST_SEAM) != 1:
        raise ValueError('test seam marker drifted')
    text = text.replace(TEST_SEAM, PRIVATE_SEAM, 1)
    return text


def prepare(root, output):
    root, output = root.resolve(), output.resolve()
    try:
        output.relative_to(root / 'src')
    except ValueError:
        pass
    else:
        raise ValueError('output must not be within production src/')
    source_path = root / RELATIVE_SOURCE
    original = source_path.read_text()
    generated = testable_source(original)
    output.mkdir(parents=True, exist_ok=True)
    generated_path = output / 'LegacyHostingContentTestable.swift'
    generated_path.write_text(generated)
    sha = lambda text: hashlib.sha256(text.encode()).hexdigest()
    metadata = {
        'commit': subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True).strip(),
        'production_source': str(RELATIVE_SOURCE),
        'production_source_sha256': sha(original),
        'generated_source_sha256': sha(generated),
        'allowed_transformation': 'generated copy widens contentSizeChanged access, de-privates the LegacyHostedSize payload, and adds a probe-only generation getter; production bytes are untouched',
        'probe_inputs_sha256': {
            relative: hashlib.sha256((root / relative).read_bytes()).hexdigest()
            for relative in (
                'scripts/ios15-size-delivery-probe/ProbeApp.swift',
                'scripts/prepare_ios15_size_delivery_probe.py',
                'scripts/run_ios15_size_delivery_probe.sh',
            )
        },
        'limits': 'Notification contract probe with explicit samples, not a rendering reproduction',
    }
    (output / 'inputs.json').write_text(json.dumps(metadata, indent=2) + '\n')
    return metadata


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(prepare(args.root, args.output), indent=2))
