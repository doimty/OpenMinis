#!/usr/bin/env python3
"""Execute the observation-only boundary validator and its negative controls."""
from pathlib import Path
import unittest
from unittest.mock import patch
from check_source import ROOT, SOURCES, check


class IsolationTests(unittest.TestCase):
    def test_current_sources_restore_the_locked_baseline(self):
        result = check(ROOT)
        self.assertEqual(set(result), set(SOURCES))
        self.assertTrue(all(row['restores_baseline'] for row in result.values()))

    def run_with_mutation(self, path, old, new):
        original = Path.read_text
        data = path.read_text()
        self.assertEqual(data.count(old), 1)
        mutated = data.replace(old, new)
        def read(p, *args, **kwargs):
            return mutated if p == path else original(p, *args, **kwargs)
        with patch.object(Path, 'read_text', read):
            check(ROOT)

    def test_unrelated_scroll_behavior_change_is_rejected(self):
        path = ROOT / 'src/ios/Agent/MessageList/CollectionViewMessageListV3.swift'
        with self.assertRaisesRegex(ValueError, 'non-diagnostic source change'):
            self.run_with_mutation(path,
                'var scrollMode: ScrollMode = .autoScrolling {',
                'var scrollMode: ScrollMode = .userBrowsing {')

    def test_observer_cannot_force_an_extra_text_measure(self):
        path = ROOT / 'src/ios/Views/Chat/SelectableMarkdownView.swift'
        with self.assertRaisesRegex(ValueError, 'active UI operation'):
            self.run_with_mutation(path,
                '        let context = reentryDiagnosticContext(self)\n',
                '        _ = sizeThatFits(.zero)\n        let context = reentryDiagnosticContext(self)\n')

    def test_missing_debug_gate_is_rejected(self):
        path = ROOT / 'src/ios/Shared/AppLogger.swift'
        with self.assertRaisesRegex(ValueError, 'not DEBUG-gated'):
            self.run_with_mutation(path,
                '// REENTRY-DIAG-BEGIN\n#if DEBUG\n',
                '// REENTRY-DIAG-BEGIN\n#if true\n')


if __name__ == '__main__':
    unittest.main(verbosity=2)
