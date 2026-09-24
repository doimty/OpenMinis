#!/usr/bin/env python3
"""Verify native-policy source adaptation; these are not native executions."""
from pathlib import Path
import tempfile
import unittest
import run_tests as runner


class RunnerSourceTests(unittest.TestCase):
    def test_unmutated_complete_class_restores_exact_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = runner.prepare(root)
            actual = (root / 'MessageListLayout.swift').read_bytes().replace(
                b'import Foundation\nimport CoreGraphics\n', b'import UIKit\n', 1)
            self.assertEqual(actual, runner.source(runner.LAYOUT))
            self.assertEqual(result['layoutSourceSHA256'], result['compiledSourceSHA256'])
            self.assertIsNone(result['mutation'])
            self.assertEqual(result['nativeExecution'], 'NOT_RUN')

    def test_precalc_ablation_changes_only_the_admission_predicate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = runner.prepare(root, mutation='precalc-blind')
            expected = runner.source(runner.LAYOUT).replace(b'if hasCached || hasPrecalc {', b'if hasCached {')
            actual = (root / 'MessageListLayout.swift').read_bytes().replace(
                b'import Foundation\nimport CoreGraphics\n', b'import UIKit\n', 1)
            self.assertEqual(actual, expected)
            self.assertNotEqual(result['layoutSourceSHA256'], result['compiledSourceSHA256'])

    def test_cancellation_ablation_is_real_source_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = runner.prepare(Path(tmp), mutation='keep-pending')
            self.assertNotEqual(result['layoutSourceSHA256'], result['compiledSourceSHA256'])
            self.assertEqual(result['mutation'], 'keep-pending')

    def test_mutation_refuses_a_source_without_its_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(AssertionError):
                runner.prepare(Path(tmp), revision='b47cb87a33b4991c50396f727e39fb99387bfa60', mutation='precalc-blind')


if __name__ == '__main__':
    unittest.main(verbosity=2)
