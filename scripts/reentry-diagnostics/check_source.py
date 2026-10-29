#!/usr/bin/env python3
"""Check instrumentation isolation and extract the REAL scalar collector for native tests.

This is a source-restoration gate, not an iOS rendering acceptance test.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[2]
BASELINE = '1f472752f79700e70bf7184006682a0db49bcc07'
SOURCES = (
    'src/ios/Shared/AppLogger.swift',
    'src/ios/Shared/LegacyHostingContent.swift',
    'src/ios/Agent/MessageList/MessageListInfrastructure.swift',
    'src/ios/Agent/MessageList/MessageListLayout.swift',
    'src/ios/Agent/MessageList/CollectionViewMessageListV3.swift',
    'src/ios/Views/Chat/SelectableMarkdownView.swift',
    'src/ios/Views/Chat/AIChatView.swift',
)
BLOCK = re.compile(r'(?m)^[ \t]*// REENTRY-DIAG-BEGIN\n.*?^[ \t]*// REENTRY-DIAG-END\n?', re.S)


def normalize(source: str) -> str:
    return '\n'.join(line.rstrip() for line in source.splitlines() if line.strip())


def check(root: Path) -> dict:
    result = {}
    for name in SOURCES:
        source = (root / name).read_text()
        baseline = subprocess.check_output(['git', '-C', str(root), 'show', BASELINE + ':' + name], text=True)
        blocks = BLOCK.findall(source)
        if not blocks or source.count('REENTRY-DIAG-BEGIN') != len(blocks):
            raise ValueError('unbalanced/missing diagnostic delimiters: ' + name)
        stripped = BLOCK.sub('', source)
        if normalize(stripped) != normalize(baseline):
            raise ValueError('non-diagnostic source change: ' + name)
        for block in blocks:
            if not re.search(r'// REENTRY-DIAG-BEGIN\n\s*#if DEBUG\n', block):
                raise ValueError('hook is not DEBUG-gated: ' + name)
            # A diagnostic may read the existing result, never trigger another
            # layout/measure or change a viewport to "stabilize" the picture.
            for forbidden in ('setContentOffset(', 'invalidateIntrinsicContentSize(',
                              'invalidateLayout(', 'layoutIfNeeded(', 'sizeThatFits('):
                if forbidden in block:
                    raise ValueError('active UI operation inside observation: ' + name + ' ' + forbidden)
        result[name] = {'sha256': hashlib.sha256(source.encode()).hexdigest(),
                        'blocks': len(blocks), 'restores_baseline': True}
    return result


def extract_helper(root: Path) -> str:
    source = (root / SOURCES[0]).read_text()
    blocks = BLOCK.findall(source)
    if len(blocks) != 1:
        raise ValueError('collector extraction must have exactly one source block')
    return 'import Foundation\n' + blocks[0]


def make_noop_mutant(source: str) -> str:
    start = source.index('    func record(')
    body = source.index(' {\n', start)
    end = source.index('    private func appendTraceFile', body)
    return source[:body] + ' {\n    }\n\n' + source[end:]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    manifest = {'kind': 'observation-only-source-restoration', 'baseline': BASELINE,
                'source': check(args.root)}
    helper = extract_helper(args.root)
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / 'Recorder.swift').write_text(helper)
    (args.output / 'Recorder-noop.swift').write_text(make_noop_mutant(helper))
    manifest['collector_sha256'] = hashlib.sha256(helper.encode()).hexdigest()
    (args.output / 'source.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print('PASS: all 7 production files restore baseline after removing DEBUG observation blocks')


if __name__ == '__main__':
    main()
