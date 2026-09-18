#!/usr/bin/env python3
"""Regression cases for the compiler-probe diagnostic summary."""

import unittest

from ios15_build_log import extract_errors


class DiagnosticExtractionTests(unittest.TestCase):
    def test_source_comment_is_not_a_diagnostic(self):
        line = ' 62 | /// Build an error envelope: {ok:false, tool, action, error:{code,message}, timestamp}.'
        self.assertEqual(extract_errors(line), [])

    def test_echoed_string_literal_is_not_a_diagnostic(self):
        self.assertEqual(extract_errors(' 5 | let text = "an error: example"'), [])

    def test_swift_diagnostic_preserves_location(self):
        line = "/Users/runner/BackupRestoreView.swift:305:13: error: 'LabeledContent' is only available in iOS 16.0 or newer"
        self.assertEqual(extract_errors(line), [line])

    def test_file_diagnostic_without_column(self):
        line = 'Sources/Example.swift:10: error: cannot find symbol'
        self.assertEqual(extract_errors(line), [line])

    def test_warnings_and_notes_are_not_errors(self):
        self.assertEqual(extract_errors('Example.swift:2:3: warning: example\nExample.swift:2:3: note: example'), [])

    def test_unlocated_tools_and_fatal_errors(self):
        lines = [
            'error: unable to resolve package',
            'fatal error: module not found',
            'xcodebuild: error: Could not resolve package dependencies:',
            'clang: error: linker command failed with exit code 1',
            '<unknown>:0: error: cannot open file',
            'src/example.c:1:10: fatal error: header.h file not found',
        ]
        self.assertEqual(extract_errors('\n'.join(lines)), lines)

    def test_linker_failures_are_not_lost(self):
        lines = [
            'ld: library not found for -lexample',
            'ld: symbol(s) not found for architecture arm64',
        ]
        self.assertEqual(extract_errors('\n'.join(lines)), lines)
        self.assertEqual(extract_errors('ld: warning: ignoring duplicate libraries'), [])

    def test_observed_comment_repetitions_do_not_inflate_count(self):
        comment = ' 62 | /// Build an error envelope: {ok:false, tool, action, error:{code,message}, timestamp}.'
        errors = [f'/Users/runner/BackupRestoreView.swift:{300+i}:13: error: unavailable API' for i in range(24)]
        self.assertEqual(extract_errors('\n'.join([comment] * 18 + errors)), errors)


if __name__ == '__main__':
    unittest.main()
