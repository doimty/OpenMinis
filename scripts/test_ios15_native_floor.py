#!/usr/bin/env python3
"""Fixture-only native-floor tests. Never download or execute Apple artifacts."""

import tempfile
import unittest
from pathlib import Path

from audit_ios15_native_floor import (
    audit_dependencies,
    audit_metadata,
    audit_native_dir,
)

MODERN = """/tmp/F.framework/F:
Mach header
      magic cputype cpusubtype caps filetype ncmds sizeofcmds flags
MH_MAGIC_64 ARM64 ALL 0x00 DYLIB 2 72 NOUNDEFS DYLDLINK
Load command 0
      cmd LC_BUILD_VERSION
  cmdsize 32
 platform IOS
    minos 15.0
      sdk 26.2
   ntools 1
     tool LD
  version 1234.5
Load command 1
          cmd LC_ID_DYLIB
      cmdsize 48
         name @rpath/F.framework/F (offset 24)
   time stamp 1
      current version 1.0.0
compatibility version 1.0.0
"""
LEGACY = """/tmp/F.framework/F:
Mach header
      magic cputype cpusubtype caps filetype ncmds sizeofcmds flags
MH_MAGIC_64 ARM64 ALL 0x00 DYLIB 2 72 NOUNDEFS DYLDLINK
Load command 0
      cmd LC_VERSION_MIN_IPHONEOS
  cmdsize 16
      version 15.0
      sdk 26.2
"""
STATIC = """/tmp/libS.a(member_arm64.o):
Mach header
      magic cputype cpusubtype caps filetype ncmds sizeofcmds flags
MH_MAGIC_64 ARM64 ALL 0x00 OBJECT 2 72 NOUNDEFS
Load command 0
      cmd LC_BUILD_VERSION
  cmdsize 32
 platform IOS
    minos 15.0
      sdk 26.2
   ntools 1
     tool LD
  version 1234.5
/tmp/libS.a(member_x86.o):
Mach header
      magic cputype cpusubtype caps filetype ncmds sizeofcmds flags
MH_MAGIC_64 X86_64 ALL 0x00 OBJECT 2 72 NOUNDEFS
Load command 0
      cmd LC_BUILD_VERSION
  cmdsize 32
 platform IOS
    minos 15.0
      sdk 26.2
   ntools 1
     tool LD
  version 1234.5
"""
PLIST_OK = {
    'CFBundleExecutable': 'F',
    'MinimumOSVersion': '15.0',
    'CFBundleSupportedPlatforms': ['iPhoneOS'],
}
EXPORTS = """F(F):
  - _create_vad_instance
  - _set_vad_callback
  - _set_vad_sample_rate
  - _set_vad_threshold
  - _set_vad_model
  - _process_vad_audio
  - _dyld_stub_binder
  - framework RealTimeCutVADCXXLibrary
  - /usr/lib/libSystem.B.dylib
"""
MIN_15_6 = MODERN.replace('minos 15.0', 'minos 15.6')
LEGACY_15_6 = LEGACY.replace('version 15.0', 'version 15.6')
MODERN_MACOS = MODERN.replace(' platform IOS', ' platform MACOS')
MODERN_X86 = MODERN.replace('ARM64 ALL', 'X86_64 ALL').replace(' arm64', ' x86_64')
MODERN_NO_MIN = MODERN.replace('    minos 15.0\n', '').replace('      sdk 26.2\n', '')
PLIST_15_6 = dict(PLIST_OK, MinimumOSVersion='15.6')
PLIST_NO_MIN = {key: value for key, value in PLIST_OK.items()
                if key != 'MinimumOSVersion'}
PLIST_BAD_PLATFORM = dict(PLIST_OK, CFBundleSupportedPlatforms=['MacOSX'])


class FloorTests(unittest.TestCase):
    def test_sdk_is_not_the_minimum(self):
        result = audit_metadata(MODERN, 'arm64\n', PLIST_OK, output=True)
        self.assertTrue(result['ok'], result['errors'])
        self.assertEqual(result['images'][0]['minimum'], '15.0')
        self.assertEqual(result['images'][0]['sdk'], '26.2')

    def test_higher_minimum_is_not_approved(self):
        result = audit_metadata(MIN_15_6, 'arm64', PLIST_OK, output=True)
        self.assertFalse(result['ok'])
        self.assertTrue(any('15.6' in error for error in result['errors']))

    def test_legacy_load_command_minimum_parsed(self):
        result = audit_metadata(LEGACY, 'arm64', PLIST_OK, output=True)
        self.assertTrue(result['ok'], result['errors'])
        self.assertEqual(result['images'][0]['minimum'], '15.0')

    def test_legacy_higher_minimum_rejected(self):
        result = audit_metadata(LEGACY_15_6, 'arm64', PLIST_OK, output=True)
        self.assertFalse(result['ok'])
        self.assertTrue(any('15.6' in error for error in result['errors']))

    def test_wrong_platform_rejected(self):
        result = audit_metadata(MODERN_MACOS, 'arm64', PLIST_OK, output=True)
        self.assertFalse(result['ok'])
        self.assertTrue(any('MACOS' in error for error in result['errors']))

    def test_wrong_architecture_rejected(self):
        result = audit_metadata(MODERN_X86, 'x86_64', PLIST_OK, output=True)
        self.assertFalse(result['ok'])
        self.assertTrue(any('x86_64' in error for error in result['errors']))

    def test_missing_minimum_rejected(self):
        result = audit_metadata(MODERN_NO_MIN, 'arm64', PLIST_OK, output=True)
        self.assertFalse(result['ok'])
        self.assertTrue(any('minimum' in error for error in result['errors']))

    def test_static_member_with_wrong_arch_rejected(self):
        result = audit_metadata(STATIC, 'arm64\narm64\nx86_64', None, output=True)
        self.assertFalse(result['ok'])
        self.assertTrue(any('x86_64' in error for error in result['errors']))

    def test_plist_higher_minimum_rejected(self):
        result = audit_metadata(MODERN, 'arm64', PLIST_15_6, output=True)
        self.assertFalse(result['ok'])
        self.assertTrue(any('15.6' in error for error in result['errors']))

    def test_plist_missing_minimum_rejected(self):
        result = audit_metadata(MODERN, 'arm64', PLIST_NO_MIN, output=True)
        self.assertFalse(result['ok'])
        self.assertTrue(any('MinimumOSVersion' in error for error in result['errors']))

    def test_plist_unknown_platform_rejected(self):
        result = audit_metadata(MODERN, 'arm64', PLIST_BAD_PLATFORM, output=True)
        self.assertFalse(result['ok'])
        self.assertTrue(any('platform' in error for error in result['errors']))

    def test_continuing_pcm_callback_exports_present(self):
        parsed = audit_dependencies(EXPORTS)
        self.assertTrue(parsed['export_lines'])
        self.assertTrue(any('set_vad_callback' in line for line in parsed['export_lines']))
        self.assertTrue(any('process_vad_audio' in line for line in parsed['export_lines']))
        self.assertTrue(parsed['library_lines'])

    def test_structural_probe_finds_framework(self):
        with tempfile.TemporaryDirectory() as tmp:
            framework = Path(tmp) / 'RealTimeCutVADCXXLibrary.framework'
            framework.mkdir()
            (framework / 'RealTimeCutVADCXXLibrary').write_bytes(b'x')
            (framework / 'Info.plist').write_bytes(b'x')
            result = audit_native_dir(tmp, want='framework')
            self.assertEqual(result['errors'], [])
            self.assertEqual(len(result['results']), 1)
            self.assertEqual(result['results'][0]['kind'], 'framework')

    def test_structural_probe_reports_empty_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = audit_native_dir(tmp, want='framework')
            self.assertTrue(any('no frameworks found' in error for error in result['errors']))

    @unittest.skipUnless(__import__('os').uname().sysname != 'Darwin',
                         'Linux refusal control only runs off-mac')
    def test_probe_refuses_non_darwin_before_any_work(self):
        import os, subprocess, sys
        env = dict(os.environ)
        env.pop('RUNNER_TEMP', None)
        env.pop('DEVELOPER_DIR', None)
        result = subprocess.run([sys.executable.replace('python3', 'bash'),
                                 str(Path(__file__).resolve().parent / 'probe_ios15_vad.sh')],
                                env=env, capture_output=True, text=True, timeout=15)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('macOS', result.stderr)
        # No evidence directory was created by the probe itself.
        self.assertFalse(any(Path('/tmp').glob('vad-probe-*')))


class RealOutputRegressionTests(unittest.TestCase):
    def test_real_otool_l_does_not_require_h(self):
        text = MODERN.replace('Mach header\n', '').replace(
            '      magic cputype cpusubtype caps filetype ncmds sizeofcmds flags\n', '').replace(
            'MH_MAGIC_64 ARM64 ALL 0x00 DYLIB 2 72 NOUNDEFS DYLDLINK\n', '')
        result = audit_metadata(text, 'arm64', PLIST_OK, output=True)
        self.assertTrue(result['ok'], result['errors'])

    def test_earlier_high_archive_member_is_not_overwritten(self):
        text = MODERN.replace('/tmp/F.framework/F', '/tmp/S.a(duplicate.o)').replace(
            'minos 15.0', 'minos 16.0') + MODERN.replace('/tmp/F.framework/F', '/tmp/S.a(duplicate.o)')
        result = audit_metadata(text, 'arm64', None, output=True)
        self.assertFalse(result['ok'])
        self.assertEqual(len(result['images']), 2)

    def test_simulator_is_not_device(self):
        result = audit_metadata(MODERN.replace('platform IOS', 'platform IOSSIMULATOR'),
                                'arm64', PLIST_OK, output=True)
        self.assertFalse(result['ok'])

    def test_patch_minimum_cannot_be_dropped(self):
        result = audit_metadata(MODERN.replace('minos 15.0', 'minos 15.0.1'),
                                'arm64', PLIST_OK, output=True)
        self.assertFalse(result['ok'])

    def test_unknown_architecture_is_not_approved(self):
        no_arch = MODERN.replace('MH_MAGIC_64 ARM64 ALL', 'MH_MAGIC_64  ALL')
        result = audit_metadata(no_arch, '', PLIST_OK, output=True)
        self.assertFalse(result['ok'])

    def test_duplicate_minimum_is_not_last_value_wins(self):
        text = MODERN.replace('minos 15.0', 'minos 16.0\n    minos 15.0')
        self.assertFalse(audit_metadata(text, 'arm64', PLIST_OK, output=True)['ok'])

    def test_toolchain_prefix_is_not_added_twice(self):
        script = (Path(__file__).parent / 'probe_ios15_vad.sh').read_text()
        self.assertFalse('^Xcode ${EXPECTED_XCODE' in script,
                         'workflow value already includes the Xcode prefix')
        self.assertFalse('Build version ${EXPECTED_XCODE_BUILD' in script,
                         'workflow value already includes the Build version prefix')

    def test_metadata_failure_reaches_cli_exit_code(self):
        import subprocess, sys
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'load.txt').write_text(MIN_15_6)
            (root / 'arch.txt').write_text('arm64')
            result = subprocess.run([sys.executable,
                str(Path(__file__).parent / 'audit_ios15_native_floor.py'),
                '--input', str(root / 'load.txt'), '--arch', str(root / 'arch.txt')],
                capture_output=True, text=True, timeout=10)
            self.assertNotEqual(result.returncode, 0)


if __name__ == '__main__':
    unittest.main()