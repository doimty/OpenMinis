#!/usr/bin/env python3
"""Source/probe guards, not the native runtime-name test."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from prepare_debug_bridge_probe import DEFAULT_BASELINE, SWIFT_SOURCES, OFFLOAD_SOURCE, INPUT_PATHS, dispatcher_slice, prepare, validate_report
ROOT=Path(__file__).resolve().parents[1]
PROBE_INFRA_FIXTURE='scripts/run_debug_bridge_probe.sh'


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

    def test_worktree_bytes_cannot_claim_head_commit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'repo';root.mkdir()
            for p in INPUT_PATHS:
                target=root/p;target.parent.mkdir(parents=True,exist_ok=True)
                target.write_bytes((ROOT/p).read_bytes())
            env=dict(os.environ,GIT_AUTHOR_NAME='Probe Fixture',GIT_AUTHOR_EMAIL='probe@example.invalid',
                     GIT_COMMITTER_NAME='Probe Fixture',GIT_COMMITTER_EMAIL='probe@example.invalid')
            def git(*args):
                return subprocess.check_output(['git','-C',str(root),*args],env=env,stderr=subprocess.STDOUT,text=True).strip()
            git('init','-q');git('add','--',*INPUT_PATHS);git('commit','-qm','fixture')
            head=git('rev-parse','HEAD')
            clean=prepare(root,Path(tmp)/'clean',head)
            self.assertEqual(clean['candidate_commit'],head)
            self.assertEqual(clean['candidate_source_kind'],'commit')
            path=root/SWIFT_SOURCES[0];original=path.read_bytes()
            path.write_bytes(original+b'\n// provenance fixture\n')
            dirty=prepare(root,Path(tmp)/'dirty',head)
            self.assertIsNone(dirty['candidate_commit'])
            self.assertEqual(dirty['candidate_head_commit'],head)
            self.assertEqual(dirty['candidate_source_kind'],'worktree')
            self.assertNotEqual(dirty['candidate_input_sha256'],clean['candidate_input_sha256'])
            self.assertTrue(any(SWIFT_SOURCES[0] in p for p in dirty['candidate_dirty_inputs']))
            path.write_bytes(original)
            (root/PROBE_INFRA_FIXTURE).write_text((root/PROBE_INFRA_FIXTURE).read_text()+'\n# provenance fixture\n')
            infra=prepare(root,Path(tmp)/'infra',head)
            self.assertIsNone(infra['candidate_commit'])
            self.assertNotEqual(infra['candidate_input_sha256'],clean['candidate_input_sha256'])


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

    def test_partial_baseline_lookup_is_not_full_two_class_red(self):
        for key in ('coldLogReaderLookup','warmDispatcherIdentity','warmLogReaderIdentity'):
            r=self.report('baseline');r[key]=True
            with self.subTest(key=key),self.assertRaisesRegex(ValueError,'two-class'):
                validate_report(r,'nonce','baseline')

    def test_baseline_names_must_match_real_namespaced_defect(self):
        for key in ('runtimeDispatcherName','runtimeLogReaderName'):
            r=self.report('baseline');r[key]='Unexpected'
            with self.subTest(key=key),self.assertRaisesRegex(ValueError,'namespaced'):
                validate_report(r,'nonce','baseline')


if __name__=='__main__':unittest.main(verbosity=2)
