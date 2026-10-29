#!/usr/bin/env python3
"""Tooling behavior tests, not native UIKit test results."""
from pathlib import Path
import importlib.util
import plistlib
import subprocess
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def module():
    spec = importlib.util.spec_from_file_location('device_prepare', HERE / 'prepare_device_probe.py')
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


class DevicePreparationTests(unittest.TestCase):
    def test_overlay_restores_exact_baseline_and_never_slices_rendering_classes(self):
        tool = module()
        with tempfile.TemporaryDirectory() as tmp:
            stage = Path(tmp)
            original = {}
            for name in tool.BASELINE_PATHS:
                data = subprocess.check_output(['git', '-C', str(ROOT), 'show', tool.BASELINE + ':' + name])
                original[name] = data
                p = stage / name
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(data)
            result = tool.prepare_sources(stage, ROOT, HERE / 'DeviceProbe.swift')
            self.assertEqual(result['baselineCommit'], tool.BASELINE)
            for name in tool.RENDER_PATHS:
                self.assertEqual((stage / name).read_bytes(), original[name])
            self.assertEqual(tool.restore_sources(stage), original)
            self.assertIn('NativeProvisionalHeightApp', (stage / tool.DEBUG_PATH).read_text())
            self.assertEqual((stage / tool.MAIN_PATH).read_text().count('@main'), 0)
            with self.assertRaises(ValueError):
                tool.prepare_sources(stage, ROOT, HERE / 'DeviceProbe.swift')

    def test_changed_production_source_is_rejected_before_any_overlay(self):
        tool = module()
        with tempfile.TemporaryDirectory() as tmp:
            stage = Path(tmp)
            for name in tool.BASELINE_PATHS:
                data = subprocess.check_output(['git', '-C', str(ROOT), 'show', tool.BASELINE + ':' + name])
                p = stage / name
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(data)
            changed = stage / tool.RENDER_PATHS[0]
            changed.write_text(changed.read_text() + '\n// unexpected functional source\n')
            before = (stage / tool.MAIN_PATH).read_bytes()
            with self.assertRaisesRegex(ValueError, 'baseline'):
                tool.prepare_sources(stage, ROOT, HERE / 'DeviceProbe.swift')
            self.assertEqual((stage / tool.MAIN_PATH).read_bytes(), before)

    def test_probe_bundle_is_separate_and_source_bundle_unchanged(self):
        tool = module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app = root / 'original' / 'Minis.app'
            app.mkdir(parents=True)
            info = {'CFBundleIdentifier': 'com.openminis.app', 'CFBundleExecutable': 'Minis',
                    'MinimumOSVersion': '15.0', 'CFBundleVersion': '1',
                    'UIApplicationSceneManifest': {'UIApplicationSupportsMultipleScenes': True},
                    'CFBundleURLTypes': [{'CFBundleURLSchemes': ['minis']}]}
            (app / 'Info.plist').write_bytes(plistlib.dumps(info))
            (app / 'Minis').write_bytes(b'fixture executable')
            (app / 'PlugIns' / 'MinisShare.appex').mkdir(parents=True)
            output = root / 'copy'
            probe = tool.prepare_bundle(app, output, 'a' * 40)
            result = plistlib.loads((probe / 'Info.plist').read_bytes())
            self.assertEqual(result['CFBundleIdentifier'], 'com.openminis.layoutprobe')
            self.assertTrue(result['MinisNativeHeightProbe'])
            self.assertTrue(result['MinisReentryDiagnostics'])
            self.assertEqual(result['MinisDiagnosticCommit'], 'a' * 40)
            self.assertNotIn('UIApplicationSceneManifest', result)
            self.assertNotIn('CFBundleURLTypes', result)
            self.assertFalse((probe / 'PlugIns').exists())
            self.assertEqual(plistlib.loads((app / 'Info.plist').read_bytes()), info)
            self.assertTrue((app / 'PlugIns' / 'MinisShare.appex').is_dir())
            with self.assertRaises(ValueError):
                tool.prepare_bundle(app, output, 'a' * 40)


if __name__ == '__main__':
    unittest.main(verbosity=2)
