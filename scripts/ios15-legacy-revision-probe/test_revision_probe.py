#!/usr/bin/env python3
"""Extraction/validator contracts for the baseline/C1/C3 revision probe.

Run anywhere (Linux included): python3 -m unittest -v test_revision_probe.py
"""
import json
import math
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

from prepare_revision_probe import (SOURCE_PATHS, SPLIT, BASELINE_COMMIT, CLEAR_OLD, CLEAR_C1,
                                    GETTER_HEAD, GETTER_TAIL, ROUTING, GATE,
                                    prepare, restore_infrastructure, restore_legacy, make_legacy)

HERE = Path(__file__).resolve().parent
ROOT = Path(__file__).resolve().parents[2]
PROBE_APP = HERE / "ProbeApp.swift"



def locked(path):
    return subprocess.check_output(["git", "-C", str(ROOT), "show", f"{BASELINE_COMMIT}:{path}"], text=True)


def normalize_swift(text):
    no_comments = re.sub(r"//[^\n]*", "", text)
    return re.sub(r"\s+", " ", no_comments).strip()


def clear_body(source):
    start = source.index("func clearCachedHeight() {")
    end = source.index("}", start) + 1
    return source[start:end]

REQUIRED_CASES = ("initial", "clear-same-content", "recovery-grow",
                  "same-size-reconfigure", "seed-recovery-control", "seed-invalidation")
VARIANTS = ("baseline", "c1", "c3")


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
    if variant not in VARIANTS:
        raise ValueError("unknown variant")
    samples = report.get("samples")
    if not isinstance(samples, list) or len(samples) != len(REQUIRED_CASES):
        raise ValueError("missing/extra samples")
    if tuple(s.get("case") for s in samples) != REQUIRED_CASES:
        raise ValueError("case identity/order mismatch")
    def number(v):
        return type(v) in (int, float) and math.isfinite(v)
    def near(v, wanted):
        return number(v) and abs(v - wanted) <= 1.5
    for s in samples:
        rows = s.get("rows")
        if not isinstance(rows, list) or [r.get("index") for r in rows] != [0, 1]:
            raise ValueError("case must carry exactly rows0/1")
        if type(s.get("preferredCalls")) is not int or s["preferredCalls"] < 0:
            raise ValueError("invalid preferred-call counter")
        for r in rows:
            for key in ("cellFrame", "hostFrame", "legacyContainerFrame"):
                frame = r.get(key)
                if not isinstance(frame, list) or len(frame) != 4 or not all(number(v) for v in frame):
                    raise ValueError("missing/nonfinite real frame: " + key)
            cache = r.get("cache")
            if not isinstance(cache, dict):
                raise ValueError("missing cache snapshot")
            for key in ("lastComputedHeight", "lastComputedWidth", "legacyMeasuredHeight",
                        "seededHeight", "seededWidth"):
                if key not in cache or (cache[key] is not None and not number(cache[key])):
                    raise ValueError("invalid cache number: " + key)
            if type(cache.get("configGeneration")) is not int or cache["configGeneration"] < 0:
                raise ValueError("invalid generation")
            if cache.get("hasWindow") is not True or cache.get("inCollection") is not True:
                raise ValueError("off-window/non-collection snapshot is INVALID")
            if "cachedHeight" not in r or (r["cachedHeight"] is not None and not number(r["cachedHeight"])):
                raise ValueError("missing/invalid layout cache")
        heights = [r["cellFrame"][3] for r in rows]
        if s["case"] in ("initial", "recovery-grow", "seed-recovery-control"):
            wanted = {"initial": 96, "recovery-grow": 160, "seed-recovery-control": 120}[s["case"]]
            if not all(near(h, wanted) for h in heights) or not all(near(r["hostFrame"][3], wanted) for r in rows):
                raise ValueError("raw control frames disagree: INVALID")
            if s["case"] == "seed-recovery-control" and not all(near(r["cache"]["legacyMeasuredHeight"], 120) for r in rows):
                raise ValueError("seed control lacks a fresh observation: INVALID")
            if s.get("hypothesis_match") is not None:
                raise ValueError("control must not masquerade as hypothesis")
        else:
            if s["case"] == "clear-same-content":
                observed = near(heights[0], 40 if variant == "baseline" else 96)
            elif s["case"] == "same-size-reconfigure":
                observed = all(near(h, 160) for h in heights) and all(near(r["cache"]["legacyMeasuredHeight"], 160) for r in rows)
            else:
                observed = near(heights[1], 176 if variant == "baseline" else 120)
            if type(s.get("hypothesis_match")) is not bool or s["hypothesis_match"] != observed:
                raise ValueError("hypothesis flag disagrees with raw height")
    return report


class ExtractionTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.out = Path(tmp.name)
        self.manifest = prepare(ROOT, self.out, HERE)

    def test_baseline_infrastructure_restores_to_locked_old_commit(self):
        text = (self.out / "ProductionInfrastructure-baseline.swift").read_text()
        old_infra = locked(SOURCE_PATHS[3]).split(SPLIT, 1)[0]
        self.assertEqual(restore_infrastructure(text), old_infra)

    def test_baseline_legacy_is_byte_copy_of_locked_old_commit(self):
        self.assertEqual((self.out / "LegacyHostingContent-baseline.swift").read_bytes(),
                         locked(SOURCE_PATHS[1]).encode())

    def test_c1_diff_is_exactly_the_declared_clear_edit(self):
        base = (self.out / "ProductionInfrastructure-baseline.swift").read_text()
        c1 = (self.out / "ProductionInfrastructure-c1.swift").read_text()
        self.assertIn(CLEAR_OLD, base)
        self.assertNotIn(CLEAR_OLD, c1)
        self.assertIn(CLEAR_C1, c1)
        self.assertEqual(c1, base.replace(CLEAR_OLD, CLEAR_C1))
        self.assertEqual(restore_infrastructure(c1),
                         restore_infrastructure(base).replace(CLEAR_OLD, CLEAR_C1))

    def test_c3_infrastructure_matches_c1(self):
        self.assertEqual((self.out / "ProductionInfrastructure-c3.swift").read_bytes(),
                         (self.out / "ProductionInfrastructure-c1.swift").read_bytes())

    def test_c3_legacy_restores_to_locked_old_commit_and_carries_generation_guard(self):
        text = (self.out / "LegacyHostingContent-c3.swift").read_text()
        self.assertIn("LegacyHostedSize", text)
        self.assertIn("payload.generation == configurationGeneration", text)
        self.assertIn("value: LegacyHostedSize(generation: generation, size: proxy.size)", text)
        self.assertEqual(restore_legacy(text), locked(SOURCE_PATHS[1]))

    def test_production_legacy_is_byte_identical_to_verified_c3(self):
        # The landed production file must equal the C3 variant that native run
        # 35596858464 verified, generated from the LOCKED pre-change commit.
        verified = make_legacy(locked(SOURCE_PATHS[1]), "c3")
        self.assertEqual((ROOT / SOURCE_PATHS[1]).read_text(), verified)

    def test_production_clear_matches_verified_c1_modulo_comments(self):
        # Production clearCachedHeight must keep the C1 statement set; only the
        # probe-tag comment was rewritten for production.
        production = (ROOT / SOURCE_PATHS[3]).read_text()
        self.assertEqual(normalize_swift(clear_body(production)), normalize_swift(CLEAR_C1))
        self.assertNotIn("legacyMeasuredSize = nil", clear_body(production))
        self.assertIn("seededHeight = nil", clear_body(production))
        self.assertIn("seededWidth = nil", clear_body(production))

    def test_all_variants_carry_readonly_getters(self):
        for variant in VARIANTS:
            text = (self.out / f"ProductionInfrastructure-{variant}.swift").read_text()
            self.assertEqual(text.count(GETTER_HEAD), 1)
            self.assertEqual(text.count(GETTER_TAIL), 1)
            self.assertIn("probeCacheSnapshot", text)
            self.assertIn('"hasWindow": hasWindow', text)
            self.assertIn('"inCollection": inCollection', text)
            self.assertNotIn('"hasWindow": window != nil', text)

    def test_companion_sources_are_byte_copies_of_locked_commit(self):
        for p in (SOURCE_PATHS[0], SOURCE_PATHS[2]):
            self.assertEqual((self.out / Path(p).name).read_bytes(), locked(p).encode())

    def test_manifest_records_locked_baseline(self):
        self.assertEqual(self.manifest["baseline_commit"], BASELINE_COMMIT)

    def test_manifest_records_hashes_and_edit_list(self):
        self.assertEqual(len(self.manifest["source_sha256"]), len(SOURCE_PATHS))
        self.assertEqual(set(self.manifest["variants"]), set(VARIANTS))
        self.assertEqual(self.manifest["variants"]["baseline"]["legacy_hosting_sha256"],
                         self.manifest["variants"]["c1"]["legacy_hosting_sha256"])
        self.assertNotEqual(self.manifest["variants"]["baseline"]["legacy_hosting_sha256"],
                            self.manifest["variants"]["c3"]["legacy_hosting_sha256"])
        self.assertIn("same_size_new_generation_policy", self.manifest["legacy_payload_contract"])
        self.assertIn("measurement_source", self.manifest["legacy_payload_contract"])

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


class ValidatorTests(unittest.TestCase):
    def sample(self, case, variant="baseline"):
        real = 96 if case in ("initial", "clear-same-content") else (120 if case in ("seed-recovery-control", "seed-invalidation") else 160)
        heights = [real, real]
        if variant == "baseline" and case == "clear-same-content":
            heights[0] = 40
        if variant == "baseline" and case == "seed-invalidation":
            heights[1] = 176
        rows = []
        for index, h in enumerate(heights):
            y = 0 if index == 0 else heights[0] + 8
            rows.append({"index": index, "cellFrame": [0, y, 428, h],
                         "legacyContainerFrame": [0, y, 428, h], "hostFrame": [0, y, 428, real],
                         "cachedHeight": float(h),
                         "cache": {"lastComputedHeight": float(h), "lastComputedWidth": 428.0,
                                   "legacyMeasuredHeight": float(real), "seededHeight": None,
                                   "seededWidth": None, "configGeneration": 3,
                                   "hasWindow": True, "inCollection": True}})
        return {"case": case, "rows": rows, "expectation": "x", "preferredCalls": 10,
                "hypothesis_match": None if case in ("initial", "recovery-grow", "seed-recovery-control") else True}

    def report(self, variant="baseline"):
        return {"os": "26.2", "variant": variant, "runID": "nonce",
                "controlsPassed": True, "passed": True,
                "samples": [self.sample(c, variant) for c in REQUIRED_CASES]}

    def test_valid_reports_pass(self):
        for variant in VARIANTS:
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

    def test_c1_semantics_differ_from_baseline(self):
        r = self.report("c1")
        self.assertEqual(validate_report(r, "nonce", "c1")["variant"], "c1")
        with self.assertRaises(ValueError):
            validate_report(r, "nonce", "baseline")

    def test_control_boolean_cannot_hide_wrong_raw_height(self):
        r = self.report(); r["samples"][0]["rows"][0]["cellFrame"][3] = 32
        with self.assertRaisesRegex(ValueError, "control"):
            validate_report(r, "nonce", "baseline")

    def test_hypothesis_false_is_valid_when_raw_observation_disagrees(self):
        r = self.report(); s = r["samples"][1]
        s["rows"][0]["cellFrame"][3] = 96; s["hypothesis_match"] = False
        self.assertIs(validate_report(r, "nonce", "baseline"), r)

    def test_false_hypothesis_summary_rejected(self):
        r = self.report(); r["samples"][1]["hypothesis_match"] = False
        with self.assertRaisesRegex(ValueError, "disagrees"):
            validate_report(r, "nonce", "baseline")

    def test_matching_reconfigure_frames_do_not_imply_rearmed_observation(self):
        r = self.report(); s = r["samples"][3]
        for row in s["rows"]:
            row["cache"]["legacyMeasuredHeight"] = None
        with self.assertRaisesRegex(ValueError, "disagrees"):
            validate_report(r, "nonce", "baseline")
        s["hypothesis_match"] = False
        self.assertIs(validate_report(r, "nonce", "baseline"), r)

    def test_seed_control_must_reestablish_observation(self):
        r = self.report(); r["samples"][4]["rows"][0]["cache"]["legacyMeasuredHeight"] = None
        with self.assertRaisesRegex(ValueError, "seed control"):
            validate_report(r, "nonce", "baseline")

    def test_nonfinite_frame_rejected(self):
        r = self.report(); r["samples"][0]["rows"][0]["hostFrame"][3] = float("nan")
        with self.assertRaises(ValueError):
            validate_report(r, "nonce", "baseline")

    def test_broken_row_snapshot_rejected(self):
        r = self.report()
        r["samples"][0]["rows"][0]["cache"] = {"legacyMeasuredHeight": 96.0}
        with self.assertRaises(ValueError):
            validate_report(r, "nonce", "baseline")


if __name__ == "__main__":
    unittest.main(verbosity=2)