#!/usr/bin/env python3
"""Source-profile tooling tests; not native policy or UIKit evidence."""
import copy
from pathlib import Path
import tempfile
import unittest
import check_candidate_source as gate


class CandidateSourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.old = {gate.LAYOUT: b'original layout\n', 'src/Other.swift': b'unchanged renderer\n'}
        self.new = b'reviewed layout repair\n'
        self.tree = {name: gate.git_blob(data) for name, data in self.old.items()}
        self.hashes = {gate.LAYOUT: gate.sha(self.old[gate.LAYOUT])}
        self.profile = {'schema': 1, 'baselineCommit': gate.BASELINE, 'productionChanges': {
            gate.LAYOUT: {'baselineSHA256': self.hashes[gate.LAYOUT], 'candidateSHA256': gate.sha(self.new)}}}
        for name, data in self.old.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(self.new if name == gate.LAYOUT else data)

    def check(self):
        return gate.check_tree(self.root, self.tree, self.hashes, self.profile)

    def test_only_reviewed_production_bytes_are_accepted(self):
        result = self.check()
        self.assertTrue(result['verified'])
        self.assertEqual(result['productionPathsCompared'], 2)
        self.assertEqual(set(result['approvedProductionChanges']), {gate.LAYOUT})

    def test_changed_allowed_file_still_needs_exact_reviewed_hash(self):
        (self.root / gate.LAYOUT).write_bytes(self.new + b'extra change')
        with self.assertRaisesRegex(ValueError, 'unapproved candidate'):
            self.check()

    def test_unrelated_renderer_change_is_rejected(self):
        (self.root / 'src/Other.swift').write_bytes(b'changed renderer')
        with self.assertRaisesRegex(ValueError, 'unrelated production'):
            self.check()

    def test_manifest_cannot_expand_production_allowlist(self):
        self.profile['productionChanges']['src/Other.swift'] = copy.deepcopy(self.profile['productionChanges'][gate.LAYOUT])
        with self.assertRaisesRegex(ValueError, 'allowlist'):
            self.check()

    def test_baseline_cannot_be_changed_to_head(self):
        self.profile['baselineCommit'] = 'a' * 40
        with self.assertRaisesRegex(ValueError, 'immutable baseline'):
            self.check()

    def test_baseline_digest_cannot_be_self_compared(self):
        self.profile['productionChanges'][gate.LAYOUT]['baselineSHA256'] = gate.sha(self.new)
        with self.assertRaisesRegex(ValueError, 'baseline hash'):
            self.check()

    def test_new_compiler_source_is_rejected(self):
        (self.root / 'src/Injected.swift').write_text('new compiler input')
        with self.assertRaisesRegex(ValueError, 'untracked compiler'):
            self.check()

    def test_missing_or_symlinked_source_is_rejected(self):
        other = self.root / 'src/Other.swift'
        other.unlink()
        with self.assertRaisesRegex(ValueError, 'missing or symlinked'):
            self.check()
        other.symlink_to(self.root / gate.LAYOUT)
        with self.assertRaisesRegex(ValueError, 'missing or symlinked'):
            self.check()


if __name__ == '__main__':
    unittest.main(verbosity=2)
