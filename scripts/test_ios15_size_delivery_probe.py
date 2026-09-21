#!/usr/bin/env python3
"""Generator invariants only. These tests do not run Apple's notification code."""
from pathlib import Path
import tempfile
import unittest

from prepare_ios15_size_delivery_probe import (
    PRIVATE_SEAM, TEST_SEAM, RELATIVE_SOURCE, CASE_NAMES,
    prepare, testable_source, restore_source, validate_native_report,
)

ROOT = Path(__file__).resolve().parents[1]


class SourceCopyTests(unittest.TestCase):
    def test_generated_copy_restores_exactly_to_production_source(self):
        source = (ROOT / RELATIVE_SOURCE).read_text()
        generated = testable_source(source)
        self.assertNotEqual(generated, source)
        self.assertIn('func contentSizeChanged(_ payload: LegacyHostedSize)', generated)
        self.assertIn('var probeConfigurationGeneration: UInt', generated)
        self.assertIn('struct LegacyHostedSize', generated)
        self.assertEqual(restore_source(generated), source)

    def test_old_private_source_only_widens_access(self):
        fixture = ('private struct LegacyHostedSize: Equatable { let generation: UInt; let size: CGSize }\n'
                   + PRIVATE_SEAM + '\n'
                   + '    override func layoutSubviews() {}\n')
        generated = testable_source(fixture)
        self.assertEqual(restore_source(generated), fixture)

    def test_missing_seam_fails(self):
        with self.assertRaisesRegex(ValueError, 'exactly one'):
            testable_source('struct SomethingElse {}')

    def test_duplicated_seam_fails(self):
        with self.assertRaisesRegex(ValueError, 'exactly one'):
            testable_source(PRIVATE_SEAM + '\n' + PRIVATE_SEAM)

    def test_generation_never_changes_production(self):
        source_path = ROOT / RELATIVE_SOURCE
        before = source_path.read_bytes()
        with tempfile.TemporaryDirectory() as tmp:
            metadata = prepare(ROOT, Path(tmp))
            generated = Path(tmp) / 'LegacyHostingContentTestable.swift'
            self.assertTrue(generated.is_file())
            self.assertEqual(metadata['production_source'], str(RELATIVE_SOURCE))
            # The generated copy only widens access + adds a probe getter:
            # restoring it must recover the production bytes exactly.
            self.assertEqual(restore_source(generated.read_text()), before.decode())
        self.assertEqual(source_path.read_bytes(), before)

    def test_generation_refuses_production_directory(self):
        with self.assertRaisesRegex(ValueError, 'production'):
            prepare(ROOT, ROOT / 'src' / 'probe-must-not-be-created')


class ReportValidationTests(unittest.TestCase):
    def report(self):
        return {'runID': 'current-run', 'os': '26.2', 'controlsPassed': True, 'passed': True,
                'cases': [{'name': name, 'repetition': repetition, 'passed': True}
                          for name in CASE_NAMES for repetition in (1, 2, 3)]}

    def test_complete_report_accepted(self):
        report = self.report()
        self.assertIs(validate_native_report(report, 'current-run'), report)

    def test_wrong_nonce_rejected(self):
        with self.assertRaisesRegex(ValueError, 'run/runtime'):
            validate_native_report(self.report(), 'old-run')

    def test_wrong_runtime_rejected(self):
        report = self.report(); report['os'] = '26.3'
        with self.assertRaisesRegex(ValueError, 'run/runtime'):
            validate_native_report(report, 'current-run')

    def test_same_count_with_duplicate_identity_rejected(self):
        report = self.report(); report['cases'][-1] = report['cases'][0].copy()
        with self.assertRaisesRegex(ValueError, 'identity'):
            validate_native_report(report, 'current-run')

    def test_summary_cannot_hide_a_failed_case(self):
        report = self.report(); report['cases'][-1]['passed'] = False
        with self.assertRaisesRegex(ValueError, 'overall'):
            validate_native_report(report, 'current-run')

    def test_real_contract_red_is_valid_not_false_green(self):
        report = self.report(); report['cases'][-1]['passed'] = False; report['passed'] = False
        self.assertIs(validate_native_report(report, 'current-run'), report)

    def test_empty_controls_cannot_pass_vacuously(self):
        report = self.report(); report['cases'] = report['cases'][6:]
        with self.assertRaisesRegex(ValueError, 'identity'):
            validate_native_report(report, 'current-run')


if __name__ == '__main__':
    unittest.main()
