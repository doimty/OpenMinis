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


def prepare(output: Path, revision=None):
    output.mkdir(parents=True, exist_ok=True)
    original = source(LAYOUT, revision)
    assert original.count(b'import UIKit\n') == 1
    adapted = original.replace(b'import UIKit\n', b'import Foundation\n')
    assert adapted.replace(b'import Foundation\n', b'import UIKit\n', 1) == original
    (output / 'MessageListLayout.swift').write_bytes(adapted)
    infrastructure = source(ITEMS, revision).decode()
    start = infrastructure.index('enum MessageListItem: Hashable {')
    end = infrastructure.index('\n\n// REENTRY-DIAG-BEGIN', start)
    item = infrastructure[start:end]
    assert item.count('enum MessageListItem:') == 1
    (output / 'MessageListItem.swift').write_text('import Foundation\n' + item + '\n')
    record = {'layoutSourceSHA256': sha(original), 'adaptedLayoutSHA256': sha(adapted),
              'itemSourceSHA256': sha(item.encode()), 'sourceRevision': revision or 'worktree',
              'sourceTransformation': 'single import UIKit -> Foundation; complete class body unchanged',
              'platformSHA256': sha((HERE / 'PolicyPlatform.swift').read_bytes()),
              'testsSHA256': sha((HERE / 'PolicyTests.swift').read_bytes()),
              'nativeExecution': 'NOT_RUN'}
    (output / 'source.json').write_text(json.dumps(record, indent=2) + '\n')
    return record


def run(output, revision=None, expected=()):
    record = prepare(output, revision)
    compiler = subprocess.check_output(['xcrun', '--find', 'swiftc'], text=True).strip()
    command = [compiler, '-swift-version', '5', '-Onone',
               str(HERE / 'PolicyPlatform.swift'), str(output / 'MessageListItem.swift'),
               str(output / 'MessageListLayout.swift'), str(HERE / 'PolicyTests.swift'),
               '-o', str(output / 'policy-tests')]
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
    parser.add_argument('--expect-failure', action='append', default=[])
    parser.add_argument('--prepare-only', action='store_true')
    args = parser.parse_args()
    if args.prepare_only:
        print(json.dumps(prepare(args.output, args.source_rev), indent=2))
    else:
        run(args.output.resolve(), args.source_rev, args.expect_failure)


if __name__ == '__main__':
    main()
