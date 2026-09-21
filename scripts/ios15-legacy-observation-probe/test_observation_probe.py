#!/usr/bin/env python3
"""Extraction/validator contracts for the collection/legacy observation probe.

Run anywhere (Linux included): python3 -m unittest -v test_observation_probe.py
"""
import json
import math
import tempfile
import unittest
from pathlib import Path

from prepare_observation_probe import (SOURCE_PATHS, CLEAR_OLD, CLEAR_CANDIDATE, GETTER_HEAD, GETTER_TAIL,
                                       ROUTING_EDIT, GATE, SPLIT, prepare, restore_production)

HERE = Path(__file__).resolve().parent
ROOT = Path(__file__).resolve().parents[2]
PROBE_APP = HERE / "ProbeApp.swift"

REQUIRED_CASES = ("initial", "clear-same-content", "recovery-grow",
                  "same-size-reconfigure", "seed-recovery-control", "seed-invalidation")


def validate_report(report, nonce, variant):
    """Structural validator. Lost controls/compile/launch are INVALID, not bug red."""
    if report.get("runID") != nonce:
        raise ValueError("stale native report")
    if report.get("variant") != variant:
        raise ValueError("variant mismatch")
    if report.get("os") != "26.2":
        raise ValueError("unexpected runtime")
    if report.get("controlsPassed") is not True or report.get("passed") is not True:
        raise ValueError("lost control cases are INVALID, not a bug red")
    if variant not in ("baseline", "candidate"):
        raise ValueError("unknown variant")
    samples = report.get("samples")
    if not isinstance(samples, list) or len(samples) != len(REQUIRED_CASES):
        raise ValueError("missing/extra samples")
    if tuple(s.get("case") for s in samples) != REQUIRED_CASES:
        raise ValueError("case identity/order mismatch")
    def number(v):
        return type(v) in (int, float) and math.isfinite(v)
    def near(v, wanted):
        return number(v) and abs(v-wanted) <= 1.5
    for s in samples:
        rows = s.get("rows")
        if not isinstance(rows, list) or [r.get("index") for r in rows] != [0, 1]:
            raise ValueError("case must carry exactly rows0/1")
        if type(s.get("preferredCalls")) is not int or s["preferredCalls"] < 0:
            raise ValueError("invalid preferred-call counter")
        for r in rows:
            for key in ("cellFrame", "hostFrame", "legacyContainerFrame"):
                frame = r.get(key)
                if not isinstance(frame,list) or len(frame)!=4 or not all(number(v) for v in frame):
                    raise ValueError("missing/nonfinite real frame: "+key)
            cache = r.get("cache")
            if not isinstance(cache,dict):
                raise ValueError("missing cache snapshot")
            for key in ("lastComputedHeight", "lastComputedWidth", "legacyMeasuredHeight", "seededHeight", "seededWidth"):
                if key not in cache or (cache[key] is not None and not number(cache[key])):
                    raise ValueError("invalid cache number: "+key)
            if type(cache.get("configGeneration")) is not int or cache["configGeneration"] < 0:
                raise ValueError("invalid generation")
            if cache.get("hasWindow") is not True or cache.get("inCollection") is not True:
                raise ValueError("off-window/non-collection snapshot is INVALID")
            if "cachedHeight" not in r or (r['cachedHeight'] is not None and not number(r['cachedHeight'])):
                raise ValueError("missing/invalid layout cache")
        heights=[r['cellFrame'][3] for r in rows]
        if s['case'] in ('initial','recovery-grow','seed-recovery-control'):
            wanted={'initial':96,'recovery-grow':160,'seed-recovery-control':120}[s['case']]
            if not all(near(h,wanted) for h in heights) or not all(near(r['hostFrame'][3],wanted) for r in rows):
                raise ValueError("raw control frames disagree: INVALID")
            if s['case']=='seed-recovery-control' and not all(near(r['cache']['legacyMeasuredHeight'],120) for r in rows):
                raise ValueError("seed control lacks a fresh observation: INVALID")
            if s.get('hypothesis_match') is not None:
                raise ValueError("control must not masquerade as hypothesis")
        else:
            if s['case']=='clear-same-content':
                observed=near(heights[0],40 if variant=='baseline' else 96)
            elif s['case']=='same-size-reconfigure':
                observed=all(near(h,160) for h in heights) and all(near(r['cache']['legacyMeasuredHeight'],160) for r in rows)
            else:
                observed=near(heights[1],176 if variant=='baseline' else 120)
            if type(s.get('hypothesis_match')) is not bool or s['hypothesis_match'] != observed:
                raise ValueError("hypothesis flag disagrees with raw height")
    return report


class ExtractionTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.out = Path(tmp.name)
        self.manifest = prepare(ROOT, self.out, HERE)

    def test_baseline_restores_to_production_bytes(self):
        baseline = (self.out / "ProductionInfrastructure-baseline.swift").read_text()
        production = (ROOT / SOURCE_PATHS[3]).read_text().split(SPLIT, 1)[0]
        self.assertEqual(restore_production(baseline), production)

    def test_candidate_diff_is_exactly_the_declared_clear_edit(self):
        baseline = (self.out / "ProductionInfrastructure-baseline.swift").read_text()
        candidate = (self.out / "ProductionInfrastructure-candidate.swift").read_text()
        self.assertIn(CLEAR_OLD, baseline)
        self.assertNotIn(CLEAR_OLD, candidate)
        self.assertIn(CLEAR_CANDIDATE, candidate)
        self.assertEqual(candidate, baseline.replace(CLEAR_OLD, CLEAR_CANDIDATE))
        self.assertEqual(restore_production(candidate),
                         restore_production(baseline).replace(CLEAR_OLD, CLEAR_CANDIDATE))

    def test_both_variants_carry_readonly_getters(self):
        for name in ("ProductionInfrastructure-baseline.swift", "ProductionInfrastructure-candidate.swift"):
            text = (self.out / name).read_text()
            self.assertEqual(text.count(GETTER_HEAD), 1)
            self.assertEqual(text.count(GETTER_TAIL), 1)
            self.assertIn("probeCacheSnapshot", text)

    def test_companion_sources_are_byte_copies(self):
        for p in SOURCE_PATHS[:3]:
            self.assertEqual((self.out / Path(p).name).read_bytes(), (ROOT / p).read_bytes())

    def test_manifest_records_hashes_and_edit_list(self):
        self.assertEqual(len(self.manifest["source_sha256"]), len(SOURCE_PATHS))
        self.assertEqual(len(self.manifest["generated_sha256"]), 5)
        self.assertIn("candidate_edits", self.manifest)
        self.assertIn("clearCachedHeight", " ".join(self.manifest["candidate_edits"]))
        self.assertNotEqual(
            self.manifest["generated_sha256"]["ProductionInfrastructure-baseline.swift"],
            self.manifest["generated_sha256"]["ProductionInfrastructure-candidate.swift"])

    def test_probe_app_adds_no_synchronous_measure_or_fabricated_heights(self):
        text = PROBE_APP.read_text()
        self.assertNotIn("systemLayoutSizeFitting", text)
        self.assertNotIn("setCachedHeight(", text)
        self.assertNotIn("cell.preferredLayoutAttributesFitting(", text)
        self.assertEqual(text.count("seedMeasuredHeight("), 1)
        self.assertEqual(text.count("clearCachedHeight()"), 2)

    def test_probe_app_uses_real_infra_and_layout(self):
        text = PROBE_APP.read_text()
        self.assertIn("MessageListViewController()", text)
        self.assertIn("cell.applyHostedContent(parent: vc)", text)
        self.assertIn("messageListLayout.invalidateHeight(at:", text)
        self.assertIn("cell.probeCacheSnapshot.json", text)
        self.assertIn("{ [weak vc, model = self.model]", text)
        self.assertIn("await dataSource?.apply(snapshot, animatingDifferences: false)", text)


class ValidatorTests(unittest.TestCase):
    def sample(self, case, variant='baseline'):
        real=96 if case in ('initial','clear-same-content') else (120 if case in ('seed-recovery-control','seed-invalidation') else 160)
        heights=[real,real]
        if variant=='baseline' and case=='clear-same-content':heights[0]=40
        if variant=='baseline' and case=='seed-invalidation':heights[1]=176
        rows=[]
        for index,h in enumerate(heights):
            y=0 if index==0 else heights[0]+8
            rows.append({'index':index,'cellFrame':[0,y,428,h],
                         'legacyContainerFrame':[0,y,428,h],'hostFrame':[0,y,428,real],
                         'cachedHeight':float(h),
                         'cache':{'lastComputedHeight':float(h),'lastComputedWidth':428.0,
                                  'legacyMeasuredHeight':float(real),'seededHeight':None,
                                  'seededWidth':None,'configGeneration':3,'hasWindow':True,'inCollection':True}})
        return {'case':case,'rows':rows,'expectation':'x','preferredCalls':10,
                'hypothesis_match':None if case in ('initial','recovery-grow','seed-recovery-control') else True}

    def report(self, variant="baseline"):
        return {"os": "26.2", "variant": variant, "runID": "nonce",
                "controlsPassed": True, "passed": True,
                "samples": [self.sample(c,variant) for c in REQUIRED_CASES]}

    def test_valid_reports_pass(self):
        for variant in ("baseline", "candidate"):
            self.assertEqual(validate_report(self.report(variant), "nonce", variant)["variant"], variant)

    def test_wrong_nonce_rejected(self):
        with self.assertRaises(ValueError):
            validate_report(self.report(), "stale", "baseline")

    def test_lost_control_is_invalid_not_red(self):
        r = self.report(); r["controlsPassed"] = False
        with self.assertRaisesRegex(ValueError, "INVALID"):
            validate_report(r, "nonce", "baseline")

    def test_wrong_runtime_rejected(self):
        r = self.report(); r["os"] = "15.0"
        with self.assertRaises(ValueError):
            validate_report(r, "nonce", "baseline")

    def test_missing_case_rejected(self):
        r = self.report(); r["samples"] = r["samples"][:-1]
        with self.assertRaises(ValueError):
            validate_report(r, "nonce", "baseline")

    def test_duplicate_case_rejected(self):
        r=self.report();r['samples'][1]=r['samples'][0]
        with self.assertRaises(ValueError):validate_report(r,'nonce','baseline')

    def test_duplicate_row_identity_rejected(self):
        r=self.report();r['samples'][0]['rows'][1]['index']=0
        with self.assertRaises(ValueError):validate_report(r,'nonce','baseline')

    def test_control_boolean_cannot_hide_wrong_raw_height(self):
        r=self.report();r['samples'][0]['rows'][0]['cellFrame'][3]=32
        with self.assertRaisesRegex(ValueError,'control'):validate_report(r,'nonce','baseline')

    def test_hypothesis_false_is_valid_when_raw_observation_disagrees(self):
        r=self.report();s=r['samples'][1]
        s['rows'][0]['cellFrame'][3]=96;s['hypothesis_match']=False
        self.assertIs(validate_report(r,'nonce','baseline'),r)

    def test_false_hypothesis_summary_rejected(self):
        r=self.report();r['samples'][1]['hypothesis_match']=False
        with self.assertRaisesRegex(ValueError,'disagrees'):validate_report(r,'nonce','baseline')

    def test_matching_reconfigure_frames_do_not_imply_rearmed_observation(self):
        r=self.report();s=r['samples'][3]
        for row in s['rows']:row['cache']['legacyMeasuredHeight']=None
        with self.assertRaisesRegex(ValueError,'disagrees'):validate_report(r,'nonce','baseline')
        s['hypothesis_match']=False
        self.assertIs(validate_report(r,'nonce','baseline'),r)

    def test_seed_control_must_reestablish_observation(self):
        r=self.report();r['samples'][4]['rows'][0]['cache']['legacyMeasuredHeight']=None
        with self.assertRaisesRegex(ValueError,'seed control'):validate_report(r,'nonce','baseline')

    def test_nonfinite_frame_rejected(self):
        r=self.report();r['samples'][0]['rows'][0]['hostFrame'][3]=float('nan')
        with self.assertRaises(ValueError):validate_report(r,'nonce','baseline')

    def test_broken_row_snapshot_rejected(self):
        r = self.report()
        r["samples"][0]["rows"][0]["cache"] = {"legacyMeasuredHeight": 96.0}
        with self.assertRaises(ValueError):
            validate_report(r, "nonce", "baseline")


if __name__ == "__main__":
    unittest.main(verbosity=2)