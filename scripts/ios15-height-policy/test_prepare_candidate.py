#!/usr/bin/env python3
"""Candidate overlay/bundle tooling, not device behavior tests."""
import io
import json
from pathlib import Path
import plistlib
import subprocess
import tarfile
import tempfile
import unittest
import prepare_candidate as candidate


class CandidatePreparationTests(unittest.TestCase):
    def stage_sources(self, root):
        archive = subprocess.check_output(['git', '-C', str(candidate.ROOT), 'archive', 'HEAD', 'src'])
        with tarfile.open(fileobj=io.BytesIO(archive)) as stream:
            stream.extractall(root, filter='data')

    def test_candidate_overlay_restores_exact_candidate_not_old_baseline(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.stage_sources(root)
            before = {name: (root / name).read_bytes() for name in candidate.probe.BASELINE_PATHS}
            result = candidate.prepare_sources(root, candidate.ROOT)
            self.assertTrue(result['exactCandidateInverseVerified'])
            self.assertEqual(candidate.probe.restore_sources(root), before)
            self.assertEqual(set(result['sourceGate']['approvedProductionChanges']), candidate.gate.ALLOWED)
            for name in candidate.probe.RENDER_PATHS:
                self.assertEqual((root / name).read_bytes(), before[name])
            self.assertEqual(result['sourceGate']['productionPathsCompared'], len(candidate.gate.tree(candidate.ROOT, candidate.gate.BASELINE)))
            self.assertFalse('exactInverseVerified' in result, 'must not impersonate the baseline profile')
            with self.assertRaises(ValueError):
                candidate.prepare_sources(root, candidate.ROOT)

    def test_unapproved_source_fails_before_overlay_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.stage_sources(root)
            main = (root / candidate.probe.MAIN_PATH).read_bytes()
            renderer = root / 'src/ios/Shared/LegacyHostingContent.swift'
            renderer.write_bytes(renderer.read_bytes() + b'\n// unexpected change\n')
            with self.assertRaisesRegex(ValueError, 'unrelated production source'):
                candidate.prepare_sources(root, candidate.ROOT)
            self.assertEqual((root / candidate.probe.MAIN_PATH).read_bytes(), main)

    def test_candidate_bundle_is_labeled_and_keeps_normal_app_separate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app = root / 'normal' / 'Minis.app'
            app.mkdir(parents=True)
            original = {'CFBundleIdentifier': 'com.openminis.app', 'CFBundleExecutable': 'Minis',
                        'MinimumOSVersion': '15.0', 'CFBundleVersion': '1',
                        'CFBundleURLTypes': [{'CFBundleURLSchemes': ['minis']}]}
            (app / 'Info.plist').write_bytes(plistlib.dumps(original))
            (app / 'Minis').write_bytes(b'fixture')
            (app / 'PlugIns' / 'Share.appex').mkdir(parents=True)
            copy = candidate.prepare_bundle(app, root / 'candidate', 'b' * 40)
            info = plistlib.loads((copy / 'Info.plist').read_bytes())
            profile = json.loads((candidate.HERE / 'candidate-source.json').read_text())
            self.assertEqual(info['CFBundleIdentifier'], 'com.openminis.layoutprobe')
            self.assertEqual(info['CFBundleDisplayName'], 'Minis Height Fix')
            self.assertTrue(info['MinisHeightRepairCandidate'])
            self.assertEqual(info['MinisHeightPolicySHA256'], profile['productionChanges'][candidate.gate.LAYOUT]['candidateSHA256'])
            self.assertNotIn('CFBundleURLTypes', info)
            self.assertFalse((copy / 'PlugIns').exists())
            self.assertEqual(plistlib.loads((app / 'Info.plist').read_bytes()), original)
            self.assertTrue((app / 'PlugIns' / 'Share.appex').is_dir())


if __name__ == '__main__':
    unittest.main(verbosity=2)
