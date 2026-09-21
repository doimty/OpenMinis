#!/usr/bin/env python3
"""Source/probe guards, not the native runtime-name test."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from prepare_debug_bridge_probe import DEFAULT_BASELINE, SWIFT_SOURCES, OFFLOAD_SOURCE, dispatcher_slice, prepare, validate_report
ROOT=Path(__file__).resolve().parents[1]


class BridgeSourceTests(unittest.TestCase):
    def test_production_change_is_only_explicit_objc_names(self):
        for relative in SWIFT_SOURCES:
            name=Path(relative).stem
            before=subprocess.check_output(['git','show',f'{DEFAULT_BASELINE}:{relative}'],cwd=ROOT,text=True)
            old=f'@objc public final class {name}: NSObject'
            new=f'@objc({name}) public final class {name}: NSObject'
            self.assertEqual(before.count(old),1)
            self.assertEqual((ROOT/relative).read_text(),before.replace(old,new,1),relative)

    def test_c_lookup_remains_the_same(self):
        before=subprocess.check_output(['git','show',f'{DEFAULT_BASELINE}:{OFFLOAD_SOURCE}'],cwd=ROOT,text=True)
        now=(ROOT/OFFLOAD_SOURCE).read_text()
        self.assertEqual(dispatcher_slice(before),dispatcher_slice(now))
        self.assertIn('NSClassFromString(@"DebugLocalDispatch")',now)
        self.assertIn('NSClassFromString(@"MinisDebugLogReader")',now)

    def test_dispatcher_boundary_drift_rejected(self):
        with self.assertRaises(ValueError):dispatcher_slice('unrelated source')

    def test_prepare_copies_production_bytes_without_rewriting(self):
        before={p:(ROOT/p).read_bytes() for p in SWIFT_SOURCES}
        with tempfile.TemporaryDirectory() as tmp:
            metadata=prepare(ROOT,tmp,DEFAULT_BASELINE)
            for p in SWIFT_SOURCES:
                self.assertEqual((Path(tmp)/'candidate'/Path(p).name).read_bytes(),before[p])
            self.assertEqual(metadata['variants']['baseline']['dispatcher_body_sha256'],metadata['variants']['candidate']['dispatcher_body_sha256'])
        for p in SWIFT_SOURCES:self.assertEqual((ROOT/p).read_bytes(),before[p])

    def test_log_reader_remains_release_available(self):
        import re
        self.assertIsNone(re.search(r'^\s*#if DEBUG', (ROOT/SWIFT_SOURCES[1]).read_text(),re.M))


class ReportTests(unittest.TestCase):
    def report(self,variant):
        green=variant=='candidate'
        return {'runID':'nonce','variant':variant,'os':'26.2','typedQualifiedControls':True,
                'selectorControls':True,'mainThreadGuard':True,'backgroundWasMain':False,'controlsPassed':True,
                'coldDispatcherLookup':green,'coldLogReaderLookup':green,'warmDispatcherIdentity':green,
                'warmLogReaderIdentity':green,'backgroundDispatcherReachedRPC':green,'passed':green,
                'backgroundError':None if green else 'DebugLocalDispatch unavailable (Release build?)',
                'runtimeDispatcherName':'DebugLocalDispatch' if green else 'Minis.DebugLocalDispatch',
                'runtimeLogReaderName':'MinisDebugLogReader' if green else 'Minis.MinisDebugLogReader'}

    def test_expected_red_is_valid_evidence(self):
        self.assertFalse(validate_report(self.report('baseline'),'nonce','baseline')['passed'])

    def test_candidate_green_accepted(self):
        self.assertTrue(validate_report(self.report('candidate'),'nonce','candidate')['passed'])

    def test_wrong_nonce_rejected(self):
        with self.assertRaises(ValueError):validate_report(self.report('candidate'),'stale','candidate')

    def test_broken_controls_are_not_a_valid_red(self):
        r=self.report('baseline');r['mainThreadGuard']=False
        with self.assertRaisesRegex(ValueError,'controls'):validate_report(r,'nonce','baseline')

    def test_wrong_baseline_failure_rejected(self):
        r=self.report('baseline');r['backgroundError']='unrelated error'
        with self.assertRaisesRegex(ValueError,'expected'):validate_report(r,'nonce','baseline')

    def test_summary_cannot_hide_failed_check(self):
        r=self.report('candidate');r['coldDispatcherLookup']=False
        with self.assertRaisesRegex(ValueError,'contradicts'):validate_report(r,'nonce','candidate')


if __name__=='__main__':unittest.main(verbosity=2)
