#!/usr/bin/env python3
"""Self-tests of extraction/protocol/negative gates; not native Swift tests."""
import importlib.util, json, subprocess, tempfile, unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('ssg_gates', ROOT / 'scripts/test_stream_stall_guard.py')
G = importlib.util.module_from_spec(spec); spec.loader.exec_module(G)


class RunnerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.units, cls.manifest, cls.source = G.extract(ROOT)

    def output(self, case, failures=()):
        ids = sorted(G.NATIVE_IDS[case]); f = set(failures)
        return '\n'.join([('FAIL' if i in f else 'PASS') + '[' + i + ']' for i in ids] + [
            f'SUMMARY case={case} pass={len(ids)-len(f)} fail={len(f)} skip=0']) + '\n'

    def test_01_real_bytes(self):
        for kind, raw in self.units.items():
            self.assertEqual(G.sha(raw), self.manifest[kind]['sha256'])
            self.assertIn(raw, self.source)

    def test_02_positive_contracts(self):
        self.assertTrue(all(G.contracts(self.units).values()))

    def test_03_wrapper_only_visibility_transform(self):
        w = G.wrapper(self.units)
        self.assertEqual(w, b'import Foundation\n' + self.units['guard'].replace(
            b'private enum StreamStallGuard', b'enum StreamStallGuard', 1) + b'\n')
        self.assertNotIn(b'private enum StreamStallGuard', w)

    def test_04_all_named_mutants_are_valid_rejections(self):
        mutants = G.mutants(self.units)
        self.assertEqual(len(mutants), 13)
        for name, kind, raw, gate, case, expected in mutants:
            with self.subTest(name=name):
                self.assertNotEqual(raw, self.units[kind])
                G.parsed(kind, raw)
                v = dict(self.units); v[kind] = raw
                self.assertFalse(G.contracts(v)[gate])
                if case:
                    self.assertIn(expected, G.NATIVE_IDS[case])

    def test_05_comments_cannot_fake_gates(self):
        v = dict(self.units)
        g = v['guard'].replace(b'cont.yield(element)', b'_ = element', 1)
        g += b'\n// cont.yield(element)\n'
        v['guard'] = g
        self.assertFalse(G.contracts(v)['guard.yields_events'])

    def test_06_valid_positive_protocol(self):
        for case in G.NATIVE_IDS:
            self.assertFalse(G.protocol(case, 0, self.output(case)))

    def test_07_named_semantic_failure_protocol(self):
        case = 'stall'; name = 'stall.snapshot_fields'
        self.assertEqual(G.protocol(case, 1, self.output(case, [name])), {name})

    def test_08_signal_is_not_rejection(self):
        with self.assertRaises(ValueError):
            G.protocol('pass', -5, self.output('pass'))

    def test_09_empty_output_not_rejection(self):
        with self.assertRaises(ValueError):
            G.protocol('pass', 1, '')

    def test_10_truncated_output_not_rejection(self):
        with self.assertRaises(ValueError):
            G.protocol('pass', 1, 'PASS[pass.clean_end]\n')

    def test_11_duplicate_ids_not_rejection(self):
        text = self.output('burst').replace('PASS[burst.count]', 'PASS[burst.ordered]')
        with self.assertRaises(ValueError):
            G.protocol('burst', 0, text)

    def test_12_unknown_ids_not_rejection(self):
        text = self.output('burst').replace('burst.count', 'invented')
        with self.assertRaises(ValueError):
            G.protocol('burst', 0, text)

    def test_13_fake_summary_not_rejection(self):
        text = self.output('burst').replace('pass=3', 'pass=999')
        with self.assertRaises(ValueError):
            G.protocol('burst', 0, text)

    def test_14_skip_not_pass(self):
        text = self.output('burst').replace('skip=0', 'skip=1')
        with self.assertRaises(ValueError):
            G.protocol('burst', 0, text)

    def test_15_wrong_mode_not_rejection(self):
        with self.assertRaises(ValueError):
            G.protocol('stall', 0, self.output('pass'))

    def test_16_wrong_exit_not_rejection(self):
        with self.assertRaises(ValueError):
            G.protocol('burst', 1, self.output('burst'))

    def test_17_invalid_explicit_runner_fails(self):
        with tempfile.TemporaryDirectory() as td:
            p = subprocess.run(['python3', str(ROOT / 'scripts/test_stream_stall_guard.py'),
                                '--runner', td + '/missing-swiftc', '--json', td + '/out.json'],
                               capture_output=True, text=True)
            self.assertEqual(p.returncode, 1)
            r = json.loads(Path(td, 'out.json').read_text(encoding="utf-8"))
            self.assertGreater(r['summary']['FAIL'], 0)

    def test_18_native_case_results_are_json_safe(self):
        # CI-only path: with swiftc the report embeds native() results; once a
        # raw set leaked into json.dumps there. case_result must keep it a list.
        report = {'native': {'cases': {c: G.case_result(c, 0, self.output(c)) for c in G.NATIVE_IDS}}}
        json.dumps(report)  # must not raise
        name = 'stall.snapshot_fields'
        self.assertEqual(G.case_result('stall', 1, self.output('stall', [name])), [name])


if __name__ == '__main__':
    unittest.main()
