#!/usr/bin/env python3
"""Self-tests of extraction/protocol/negative gates; not native Swift tests."""
import importlib.util,json,subprocess,sys,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('hang_gates',ROOT/'scripts/test_ios15_hang_boundaries.py')
G=importlib.util.module_from_spec(spec);spec.loader.exec_module(G)

class RunnerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.units,cls.manifest,_=G.extract(ROOT)
    def output(self,case,failures=()):
        ids=sorted(G.NATIVE_IDS[case]);f=set(failures)
        return '\n'.join([('FAIL' if i in f else 'PASS')+'['+i+']' for i in ids]+[
            f'SUMMARY case={case} pass={len(ids)-len(f)} fail={len(f)} skip=0'])+'\n'
    def test_01_real_bytes(self):
        for kind,v in self.manifest.items():
            if 'files' in v:
                raw=b'\n'.join((ROOT/f).read_bytes() for f in v['files'])
            else:
                raw=(ROOT/v['file']).read_bytes()
            self.assertEqual(self.units[kind],raw[v['start_byte']:v['end_byte']])
            self.assertEqual(G.sha(self.units[kind]),v['sha256'])
    def test_02_positive_contracts(self):
        self.assertTrue(all(G.contracts(self.units).values()))
    def test_03_wrapper_only_visibility_transform(self):
        w=G.wrapper(self.units)
        self.assertIn(b'internal'+self.units['registry'][len(b'private'):],w)
        self.assertIn(self.units['pending'],w)
        self.assertNotIn(self.units['animation'],w) # Platform gate isn't simulated.
    def test_04_all_named_mutants_are_valid_rejections(self):
        mutants=G.mutants(self.units)
        self.assertEqual(len(mutants),17)
        for name,kind,raw,gate,case,expected in mutants:
            with self.subTest(name=name):
                self.assertNotEqual(raw,self.units[kind])
                G.parsed(kind,raw)
                v=dict(self.units);v[kind]=raw
                self.assertFalse(G.contracts(v)[gate])
                if case:self.assertIn(expected,G.NATIVE_IDS[case])
    def test_05_comments_cannot_fake_queue_gate(self):
        v=dict(self.units)
        v['registry']=v['registry'].replace(b'forName: .sessionDidUpdate, object: nil, queue: .main',
            b'forName: .sessionDidUpdate, object: nil, queue: nil')+b'\n// object:nil,queue:.main)\n'
        self.assertFalse(G.contracts(v)['notification.activity.queue_main'])
    def test_06_valid_positive_protocol(self):
        for case in G.NATIVE_IDS:self.assertFalse(G.protocol(case,0,self.output(case)))
    def test_07_named_semantic_failure_protocol(self):
        case='loop_end';name='notification.loop_end.post_returned_while_main_held'
        self.assertEqual(G.protocol(case,1,self.output(case,[name])),{name})
    def test_08_signal_is_not_rejection(self):
        with self.assertRaises(ValueError):G.protocol('pending',-5,self.output('pending'))
    def test_09_empty_output_not_rejection(self):
        with self.assertRaises(ValueError):G.protocol('pending',1,'')
    def test_10_truncated_output_not_rejection(self):
        with self.assertRaises(ValueError):G.protocol('pending',1,'PASS[pending.once]\n')
    def test_11_duplicate_ids_not_rejection(self):
        text=self.output('weak').replace('PASS[weak.post_returned]','PASS[weak.no_callback_after_release]')
        with self.assertRaises(ValueError):G.protocol('weak',0,text)
    def test_12_unknown_ids_not_rejection(self):
        with self.assertRaises(ValueError):G.protocol('weak',0,self.output('weak').replace('weak.post_returned','invented'))
    def test_13_fake_summary_not_rejection(self):
        text=self.output('weak').replace('pass=3','pass=999')
        with self.assertRaises(ValueError):G.protocol('weak',0,text)
    def test_14_skip_not_pass(self):
        text=self.output('weak').replace('skip=0','skip=1')
        with self.assertRaises(ValueError):G.protocol('weak',0,text)
    def test_15_wrong_mode_not_rejection(self):
        with self.assertRaises(ValueError):G.protocol('activity',0,self.output('loop_end'))
    def test_16_wrong_exit_not_rejection(self):
        with self.assertRaises(ValueError):G.protocol('weak',1,self.output('weak'))
    def test_17_invalid_explicit_runner_fails(self):
        with tempfile.TemporaryDirectory() as td:
            p=subprocess.run([sys.executable,str(ROOT/'scripts/test_ios15_hang_boundaries.py'),
                '--runner',td+'/missing-swiftc','--json',td+'/out.json'],capture_output=True,text=True)
            self.assertEqual(p.returncode,1)
            r=json.loads(Path(td,'out.json').read_text(encoding="utf-8"))
            self.assertGreater(r['summary']['FAIL'],0)

if __name__=='__main__':unittest.main()
