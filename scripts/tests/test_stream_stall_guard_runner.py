#!/usr/bin/env python3
"""Self-tests of extraction/contracts/mutants for the stream-stall gate.

Not the native Swift suite: that one is `StreamStallWatchdogTests.swift` and is
executed by the gate itself on macOS.
"""
import importlib.util, json, subprocess, sys, tempfile, unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('ssg_gates', ROOT / 'scripts/test_stream_stall_guard.py')
G = importlib.util.module_from_spec(spec); spec.loader.exec_module(G)


class RunnerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.units, cls.manifest, cls.source = G.extract(ROOT)

    def test_01_real_bytes(self):
        for kind, raw in self.units.items():
            self.assertEqual(G.sha(raw), self.manifest[kind]['sha256'])
            self.assertIn(raw, self.source)

    def test_02_positive_contracts(self):
        self.assertTrue(all(G.contracts(self.units).values()))

    def test_03_all_named_mutants_are_valid_rejections(self):
        mutants = G.mutants(self.units)
        self.assertEqual(len(mutants), 8)
        for name, kind, raw, gate in mutants:
            with self.subTest(name=name):
                self.assertNotEqual(raw, self.units[kind])
                G.parsed(kind, raw)
                v = dict(self.units); v[kind] = raw
                self.assertFalse(G.contracts(v)[gate])

    def test_04_comments_cannot_fake_wait_gate(self):
        v = dict(self.units)
        r = v['reception'].replace(b'try await stallWatchdog.wait()', b'return nil', 1)
        r += b'\n// try await stallWatchdog.wait()\n'
        v['reception'] = r
        self.assertFalse(G.contracts(v)['guard.wait_no_timer'])

    def test_05_invalid_explicit_runner_fails(self):
        with tempfile.TemporaryDirectory() as td:
            p = subprocess.run([sys.executable, str(ROOT / 'scripts/test_stream_stall_guard.py'),
                                '--runner', td + '/missing-swiftc', '--json', td + '/out.json'],
                               capture_output=True, text=True)
            self.assertEqual(p.returncode, 1)
            r = json.loads(Path(td, 'out.json').read_text(encoding="utf-8"))
            self.assertGreater(r['summary']['FAIL'], 0)


if __name__ == '__main__':
    unittest.main()
