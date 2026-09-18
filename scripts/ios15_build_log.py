#!/usr/bin/env python3
"""Summarize compiler diagnostics without counting echoed source as errors.

The workflow owns xcodebuild's exit status. This report never decides whether
an otherwise failed build is allowed to pass; the complete log remains evidence.
"""

import argparse
import json
from pathlib import Path
import re


_SOURCE_ECHO = re.compile(r'^\s*(?:\d+)?\s*\|')
_DIAGNOSTIC = re.compile(
    r'^\s*(?:'
    r'.+:\d+(?::\d+)?:\s*(?:fatal )?error:'
    r'|(?:fatal )?error:'
    r'|(?:xcodebuild|clang(?:\+\+)?|swiftc|swift-frontend|ld(?:\.lld)?):\s*(?:fatal )?error:'
    r'|ld(?:\.lld)?:\s*(?:library not found|framework not found|file not found|symbol\(s\) not found|Undefined symbols)'
    r')'
)


def extract_errors(text):
    return [
        line for line in text.splitlines()
        if not _SOURCE_ECHO.match(line) and _DIAGNOSTIC.match(line)
    ]


def summarize(text, log_bytes):
    errors = extract_errors(text)
    return {
        'log_bytes': log_bytes,
        'error_lines': len(errors),
        'build_succeeded': '** BUILD SUCCEEDED **' in text,
        'build_failed': '** BUILD FAILED **' in text,
        'first_errors': errors[:80],
    }, errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('log', type=Path)
    parser.add_argument('--output-dir', required=True, type=Path)
    args = parser.parse_args()
    data = args.log.read_bytes()
    summary, errors = summarize(data.decode('utf-8', errors='replace'), len(data))
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / 'errors.txt').write_text(
        '\n'.join(errors) + ('\n' if errors else ''), encoding='utf-8')
    (args.output_dir / 'summary.json').write_text(
        json.dumps(summary, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({key: value for key, value in summary.items() if key != 'first_errors'}, indent=2))
    print('\n'.join(errors[:40]))


if __name__ == '__main__':
    main()
