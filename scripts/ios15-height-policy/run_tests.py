#!/usr/bin/env python3
"""Compile the complete actual layout with non-rendering Foundation adapters.

Only the single UIKit import is replaced. No production method is extracted,
rewritten or replaced. This runs native Swift policy tests, NOT a UIKit/renderer
simulation. A compile failure can never count as the expected behavioral red.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import platform
import subprocess
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
LAYOUT = 'src/ios/Agent/MessageList/MessageListLayout.swift'
ITEMS = 'src/ios/Agent/MessageList/MessageListInfrastructure.swift'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def source(path, revision=None):
    if revision:
        return subprocess.check_output(['git', '-C', str(ROOT), 'show', revision + ':' + path])
    return (ROOT / path).read_bytes()


def prepare(output: Path, revision=None, mutation=None):
    output.mkdir(parents=True, exist_ok=True)
    original = source(LAYOUT, revision)
    compiled = original
    if mutation == 'precalc-blind':
        old = b'if hasCached || hasPrecalc {'
        assert compiled.count(old) == 1
        compiled = compiled.replace(old, b'if hasCached {')
    elif mutation == 'keep-pending':
        old = b'        deferredHeights.removeValue(forKey: index)\n\n        // A confirmed'
        assert compiled.count(old) == 1
        compiled = compiled.replace(old, b'        // A confirmed')
    elif mutation is not None:
        raise ValueError('unknown policy mutation')
    assert compiled.count(b'import UIKit\n') == 1
    imports = b'import Foundation\nimport CoreGraphics\n'
    adapted = compiled.replace(b'import UIKit\n', imports)
    assert adapted.replace(imports, b'import UIKit\n', 1) == compiled
    (output / 'MessageListLayout.swift').write_bytes(adapted)
    infrastructure = source(ITEMS, revision).decode()
    start = infrastructure.index('enum MessageListItem: Hashable {')
    end = infrastructure.index('\n\n// REENTRY-DIAG-BEGIN', start)
    item = infrastructure[start:end]
    assert item.count('enum MessageListItem:') == 1
    (output / 'MessageListItem.swift').write_text('import Foundation\n' + item + '\n')
    record = {'layoutSourceSHA256': sha(original), 'compiledSourceSHA256': sha(compiled),
              'adaptedLayoutSHA256': sha(adapted), 'mutation': mutation,
              'itemSourceSHA256': sha(item.encode()), 'sourceRevision': revision or 'worktree',
              'sourceTransformation': 'single UIKit import -> Foundation/CoreGraphics; complete class, only declared mutation allowed',
              'platformSHA256': sha((HERE / 'PolicyPlatform.swift').read_bytes()),
              'testsSHA256': sha((HERE / 'PolicyTests.swift').read_bytes()),
              'nativeExecution': 'NOT_RUN'}
    (output / 'source.json').write_text(json.dumps(record, indent=2) + '\n')
    return record


def run(output, revision=None, expected=(), mutation=None):
    record = prepare(output, revision, mutation)
    compiler = subprocess.check_output(['xcrun', '--sdk', 'macosx', '--find', 'swiftc'], text=True).strip()
    sdk = subprocess.check_output(['xcrun', '--sdk', 'macosx', '--show-sdk-path'], text=True).strip()
    target = platform.machine() + '-apple-macosx13.0'
    record.update(compiler=compiler, sdk=sdk, target=target)
    command = [compiler, '-swift-version', '5', '-Onone', '-parse-as-library',
               '-target', target, '-sdk', sdk,
               str(HERE / 'PolicyPlatform.swift'), str(output / 'MessageListItem.swift'),
               str(output / 'MessageListLayout.swift'), str(HERE / 'PolicyTests.swift'),
               '-o', str(output / 'policy-tests')]
    record['compileCommand'] = command
    compile_result = subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    (output / 'compile.log').write_text(compile_result.stdout)
    if compile_result.returncode:
        print(compile_result.stdout)
        raise RuntimeError('native Swift compilation failed; NOT an expected bug-red')
    result = subprocess.run([str(output / 'policy-tests')], text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=30)
    (output / 'native-tests.log').write_text(result.stdout)
    print(result.stdout, end='')
    lines = [line[len('RESULT_JSON: '):] for line in result.stdout.splitlines() if line.startswith('RESULT_JSON: ')]
    if len(lines) != 1:
        raise RuntimeError('native process did not emit its complete assertion result')
    verdict = json.loads(lines[0])
    assert verdict['tests'] > 0
    assert set(verdict['failures']) == set(expected), (verdict['failures'], list(expected))
    assert result.returncode == (1 if expected else 0), result.returncode
    record.update(nativeExecution='EXECUTED', compileExit=0, runExit=result.returncode,
                  assertions=verdict, expectedFailures=list(expected), expectationVerified=True)
    (output / 'result.json').write_text(json.dumps(record, indent=2) + '\n')
    print('PASS: compiled actual policy produced the expected behavioral verdict')
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--source-rev')
    parser.add_argument('--mutation', choices=['precalc-blind', 'keep-pending'])
    parser.add_argument('--expect-failure', action='append', default=[])
    parser.add_argument('--expected-failures', type=Path)
    parser.add_argument('--prepare-only', action='store_true')
    args = parser.parse_args()
    expected = args.expect_failure
    if args.expected_failures:
        recorded = json.loads(args.expected_failures.read_text())
        assert isinstance(recorded, list) and all(isinstance(name, str) for name in recorded)
        expected += recorded
    assert len(expected) == len(set(expected)), 'repeated expected failure name'
    if args.prepare_only:
        print(json.dumps(prepare(args.output, args.source_rev, args.mutation), indent=2))
    else:
        run(args.output.resolve(), args.source_rev, expected, args.mutation)


if __name__ == '__main__':
    main()
