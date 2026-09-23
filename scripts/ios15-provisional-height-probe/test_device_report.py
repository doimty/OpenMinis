#!/usr/bin/env python3
"""Behavior tests for device_report.py. No source-string assertions."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import uuid

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from device_report import BASELINE_COMMIT, CASES, validate_report  # noqa: E402

SOURCE = "a" * 40
RUN = "11111111-2222-3333-4444-555555555555"
CV = "-cv00000000000001"
CELL = "-cell000000000001"
HOST = "-host000000000001"
CONTROLLER = "-controller000001"
DIGEST_SHORT = hashlib.sha256(b"neutral markdown with short production code fixture").hexdigest()
DIGEST_LONG = hashlib.sha256(b"neutral markdown with long production code fixture").hexdigest()
DIGEST_PLAIN = hashlib.sha256(b"neutral plain control fixture").hexdigest()


def event(seq, kind, *, owner=CV, subject=CELL, parent="none", idx=0,
          generation=1, phase="test", values=None, t=None):
    return {
        "schema": 1,
        "run": RUN,
        "commit": SOURCE,
        "seq": seq,
        "t": float(seq if t is None else t),
        "main": True,
        "kind": kind,
        "owner": owner,
        "subject": subject,
        "parent": parent,
        "idx": idx,
        "generation": generation,
        "phase": phase,
        "session": "none",
        "values": {} if values is None else values,
    }


def marker(seq, name, edge):
    return event(seq, "probe-marker", subject=CONTROLLER, idx=-1,
                 generation=0, phase=name, values={"edge": edge})


def row(reference, height=None, text_length=100):
    height = reference if height is None else height
    return {
        "cellFrame": [0, 0, 428, height],
        "hostFrame": [0, 0, 428, height],
        "textBounds": [0, 0, 404, height],
        "textLength": text_length,
        "inWindow": True,
    }


def case(name, digest, reference, height=None, start=1, end=2):
    return {
        "name": name,
        "fixtureSHA256": digest,
        "collectionID": CV,
        "reference": {"width": 404.0, "height": reference, "textLength": 100},
        "snapshot": row(reference, height),
        "startSeq": start,
        "endSeq": end,
    }


def base_report(*, reproduced=True, include_reference_owner_none=False,
                long_growth=False, include_chain=True):
    cases = [
        case("initial", DIGEST_SHORT, 120, start=1, end=2),
        case("deferred-reentry", DIGEST_SHORT, 120, 90 if reproduced else 120, start=3, end=19),
        case("deferred-settle", DIGEST_SHORT, 120, 120, start=20, end=29),
        case("attachment-growth", DIGEST_LONG, 200, start=30, end=31),
        case("reuse", DIGEST_LONG, 200, start=40, end=41),
        case("plain-control", DIGEST_PLAIN, 80, start=50, end=51),
    ]
    trace = []
    for name, start, end in ((name, c["startSeq"], c["endSeq"]) for name, c in zip(CASES, cases)):
        trace.append(marker(start, name, 0))
        if name == "deferred-reentry" and include_chain:
            trace.extend([
                event(4, "configure", subject=CELL, generation=7, phase="probe-configure"),
                event(5, "text-size", subject="-text", parent=CELL, idx=-1, generation=7,
                      phase="intrinsic-unset", values={"width": 0.0, "boundsW": 0.0, "height": 90.0}),
                event(6, "text-size", subject="-text", parent=CELL, idx=-1, generation=7,
                      phase="correction-measure", values={"width": 404.0, "boundsW": 404.0, "height": 120.0}),
                event(11, "host-size", subject=HOST, parent=CELL, idx=-1,
                      generation=70, phase="preference", values={"height": 90.0, "width": 404.0}),
                event(12, "host-size", subject=HOST, parent=CELL, idx=-1,
                      generation=70, phase="delivered", values={"height": 90.0, "width": 404.0}),
                event(13, "host-size", subject=CELL, parent="none", idx=-1,
                      generation=7, phase="cell-accepted", values={"height": 90.0, "width": 404.0}),
                event(14, "cell-return", subject=CELL, parent="none", idx=0,
                      generation=7, phase="legacy-observation", values={"height": 90.0, "width": 404.0}),
                event(15, "layout-height", subject="-layout000000001", parent="none", idx=0,
                      generation=0, phase="pre", values={"oldH": 120.0, "newH": 90.0, "decel": 1}),
            ])
        if name == "deferred-settle" and include_chain:
            trace.extend([
                event(21, "host-size", subject=HOST, parent=CELL, idx=-1,
                      generation=71, phase="preference", values={"height": 120.0, "width": 404.0}),
                event(22, "host-size", subject=HOST, parent=CELL, idx=-1,
                      generation=71, phase="delivered", values={"height": 120.0, "width": 404.0}),
                event(23, "host-size", subject=CELL, parent="none", idx=-1,
                      generation=7, phase="cell-accepted", values={"height": 120.0, "width": 404.0}),
                event(24, "cell-return", subject=CELL, parent="none", idx=0,
                      generation=7, phase="legacy-observation", values={"height": 120.0, "width": 404.0}),
                event(25, "layout-height", subject="-layout000000001", parent="none", idx=0,
                      generation=0, phase="cache", values={"oldH": 90.0, "newH": 120.0, "decel": 1}),
            ])
        if name == "attachment-growth" and long_growth:
            trace.append(event(30, "host-size", subject=HOST, parent=CELL, idx=-1,
                               generation=100, phase="delivered", values={"height": 200.0, "width": 404.0}))
        trace.append(marker(end, name, 1))
    occupied = {item["seq"] for item in trace}
    trace.extend(event(seq, "probe-idle", owner="none", idx=-1, generation=0)
                 for seq in range(1, max(occupied) + 1) if seq not in occupied)
    if include_reference_owner_none:
        trace.append(event(max(occupied) + 1, "text-size", owner="none", subject="-reference-text",
                           parent="none", idx=-1, generation=0, phase="reference",
                           values={"width": 404.0, "height": 120.0}))
    trace.sort(key=lambda item: item["seq"])
    report = {
        "schema": 1,
        "kind": "minis-provisional-height-device-probe",
        "runID": RUN,
        "sourceCommit": SOURCE,
        "baselineCommit": BASELINE_COMMIT,
        "os": "15.1.1",
        "legacyPath": True,
        "cases": cases,
        "trace": trace,
        "driverVerdict": "INCONCLUSIVE",
    }
    return report


class DeviceReportBehaviorTests(unittest.TestCase):
    def test_correlated_transient_recovery_is_baseline_reproduced(self):
        result = validate_report(base_report())
        self.assertEqual(result["status"], "BASELINE_REPRODUCED")
        self.assertTrue({4, 5, 6, 11, 12, 13, 14, 15, 21, 22, 23, 24, 25}.issubset(result["evidenceSeq"]))

    def test_driver_verdict_is_ignored(self):
        report = base_report()
        report["driverVerdict"] = "BASELINE_REPRODUCED"
        self.assertEqual(validate_report(report)["status"], "BASELINE_REPRODUCED")

    def test_finally_restored_snapshot_does_not_hide_transient_recovery(self):
        report = base_report()
        report["cases"][1]["snapshot"] = row(120)
        self.assertEqual(validate_report(report)["status"], "BASELINE_REPRODUCED")

    def test_fourteen_millisecond_transient_is_not_filtered_by_duration(self):
        report = base_report()
        for event_item in report["trace"]:
            event_item["t"] = 100.0 + event_item["seq"] * 0.0014
        self.assertEqual(validate_report(report)["status"], "BASELINE_REPRODUCED")

    def test_no_shrink_recovery_is_inconclusive(self):
        report = base_report(include_chain=False)
        report["trace"][5] = event(6, "layout-height", idx=0, values={"oldH": 80.0, "newH": 120.0})
        report["trace"][6] = event(7, "layout-height", idx=0, values={"oldH": 120.0, "newH": 160.0})
        self.assertEqual(validate_report(report)["status"], "INCONCLUSIVE")

    def test_shrink_without_complete_host_cell_association_is_inconclusive(self):
        report = base_report(include_chain=False)
        report["trace"][14] = event(15, "layout-height", subject="-layout000000001", idx=0,
                                     values={"oldH": 120.0, "newH": 90.0})
        report["trace"][24] = event(25, "layout-height", subject="-layout000000001", idx=0,
                                     values={"oldH": 90.0, "newH": 120.0})
        self.assertEqual(validate_report(report)["status"], "INCONCLUSIVE")

    def test_reference_owner_none_event_is_retained_but_not_target_chain(self):
        report = base_report(include_reference_owner_none=True)
        self.assertEqual(validate_report(report)["status"], "BASELINE_REPRODUCED")

    def test_source_commit_mismatch_is_invalid(self):
        report = base_report()
        report["sourceCommit"] = "b" * 40
        with self.assertRaises(ValueError):
            validate_report(report, expected_commit=SOURCE)

    def test_driver_capture_error_is_invalid(self):
        report = base_report()
        report["captureError"] = "frames failed"
        with self.assertRaises(ValueError):
            validate_report(report)

    def test_nonlegacy_report_is_invalid(self):
        report = base_report()
        report["legacyPath"] = False
        with self.assertRaises(ValueError):
            validate_report(report)

    def test_limit_trace_is_invalid(self):
        report = base_report()
        report["trace"].append(event(70, "limit", values={"currentGen": 1}))
        report["trace"].sort(key=lambda item: item["seq"])
        with self.assertRaises(ValueError):
            validate_report(report)

    def test_missing_marker_is_invalid(self):
        report = base_report()
        report["trace"] = [item for item in report["trace"] if not (item["kind"] == "probe-marker" and item["phase"] == "deferred-settle")]
        with self.assertRaises(ValueError):
            validate_report(report)

    def test_control_final_frame_mismatch_is_invalid(self):
        report = base_report()
        report["cases"][3]["snapshot"]["cellFrame"][3] = 66
        with self.assertRaises(ValueError):
            validate_report(report)

    def test_reference_width_mismatch_is_invalid(self):
        report = base_report()
        report["cases"][0]["snapshot"]["textBounds"][2] = 396
        with self.assertRaises(ValueError):
            validate_report(report)

    def test_fixture_collision_is_invalid(self):
        report = base_report()
        report["cases"][3]["fixtureSHA256"] = DIGEST_SHORT
        with self.assertRaises(ValueError):
            validate_report(report)

    def test_preference_without_actual_delivery_is_inconclusive(self):
        report = base_report()
        for item in report["trace"]:
            if item["kind"] == "host-size" and item["phase"] == "delivered":
                item["kind"] = "probe-idle"
        self.assertEqual(validate_report(report)["status"], "INCONCLUSIVE")

    def test_recovery_on_another_row_is_not_a_round_trip(self):
        report = base_report()
        for item in report["trace"]:
            if item["seq"] in (24, 25):
                item["idx"] = 1
        self.assertEqual(validate_report(report)["status"], "INCONCLUSIVE")

    def test_missing_configuration_identity_is_inconclusive(self):
        report = base_report()
        report["trace"][3]["kind"] = "probe-idle"
        self.assertEqual(validate_report(report)["status"], "INCONCLUSIVE")

    def test_missing_provisional_text_observation_is_inconclusive(self):
        report = base_report()
        report["trace"][4]["kind"] = "probe-idle"
        self.assertEqual(validate_report(report)["status"], "INCONCLUSIVE")

    def test_missing_trace_record_is_invalid(self):
        report = base_report()
        report["trace"] = [item for item in report["trace"] if item["seq"] != 8]
        with self.assertRaises(ValueError):
            validate_report(report)

    def test_backwards_trace_clock_is_invalid(self):
        report = base_report()
        report["trace"][14]["t"] = 0.0
        with self.assertRaises(ValueError):
            validate_report(report)

    def test_cli_exit_codes_and_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "report.json"
            path.write_text(json.dumps(base_report()))
            command = [sys.executable, str(HERE / "device_report.py"), str(path), "--commit", SOURCE]
            reproduced = subprocess.run(command, text=True, capture_output=True)
            self.assertEqual(reproduced.returncode, 0)
            self.assertTrue(reproduced.stdout.startswith("status=BASELINE_REPRODUCED"))
            report = base_report(include_chain=False)
            path.write_text(json.dumps(report))
            inconclusive = subprocess.run(command, text=True, capture_output=True)
            self.assertEqual(inconclusive.returncode, 1)
            self.assertTrue(inconclusive.stdout.startswith("status=INCONCLUSIVE"))
            path.write_text(json.dumps({"schema": 1}))
            invalid = subprocess.run(command, text=True, capture_output=True)
            self.assertEqual(invalid.returncode, 2)
            self.assertTrue(invalid.stdout.startswith("status=INVALID"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
