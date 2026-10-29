#!/usr/bin/env python3
"""Black-box tests for the ReentryDiagnostics log analyzer."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from analyze_trace import analyze_lines  # noqa: E402

COMMIT = "0123456789abcdef0123456789abcdef01234567"
RUN = "11111111-2222-3333-4444-555555555555"
SESSION = "ABCDEF12"
CV = "c001"
VM = "b001"
CELL = "ce11"
TEXT = "d001"
HOST = "e001"


def event(seq: int, kind: str, *, owner: str, subject: str = "none", parent: str = "none",
          idx: int = -1, generation: int = 0, session: str = "none",
          values: dict | None = None, phase: str = "probe") -> dict:
    return {
        "schema": 1, "run": RUN, "commit": COMMIT, "seq": seq,
        "t": 100.0 + seq / 10, "main": True, "kind": kind,
        "owner": owner, "subject": subject, "parent": parent,
        "idx": idx, "generation": generation, "phase": phase,
        "session": session, "values": values or {},
    }


def log_line(value: dict, *, wrapped: bool = False) -> str:
    prefix = "[[2026-09-22 14:00:00.123]] " if wrapped else ""
    return prefix + "[ReentryTrace] [INFO] [REENTRYDIAG] " + json.dumps(value, sort_keys=True) + "\n"


def real_callsite_events() -> list[dict]:
    """Shape copied from actual callsites, with anonymous IDs only."""
    return [
        event(1, "mount", owner=VM, session=SESSION, phase="chat-appear"),
        event(2, "viewport", owner=CV, subject=VM, session=SESSION,
              values={"offset": 0.0, "contentH": 2000.0, "viewportH": 800.0}),
        event(3, "configure", owner=CV, subject=CELL, parent=VM, idx=7, generation=4,
              values={"width": 396.0, "height": 1521.0}),
        event(4, "text-size", owner=CV, subject=TEXT, parent=CELL, generation=4,
              values={"width": 396.0, "height": 1521.0}),
        event(5, "host-size", owner=CV, subject=HOST, parent=CELL, generation=9,
              values={"width": 396.0, "height": 1516.3}, phase="preference"),
        event(6, "host-size", owner=CV, subject=HOST, parent=CELL, generation=9,
              values={"width": 396.0, "height": 1516.3}, phase="delivered"),
        event(7, "cell-return", owner=CV, subject=CELL, idx=7, generation=4,
              values={"inputH": 1521.0, "height": 1521.0}),
        event(8, "layout-height", owner=CV, subject="f001", idx=7,
              values={"oldH": 1521.0, "newH": 1354.0}),
        event(9, "layout-height", owner=CV, subject="f001", idx=7,
              values={"oldH": 1354.0, "newH": 1521.0}),
        event(10, "viewport", owner=CV, subject=VM, session=SESSION,
              values={"offset": 100.0, "contentH": 2200.0, "viewportH": 800.0}),
        event(11, "unmount", owner=VM, session=SESSION, phase="chat-disappear"),
    ]


class AnalyzerTests(unittest.TestCase):
    def assertStatus(self, lines, status):
        report = analyze_lines(lines, COMMIT)
        self.assertEqual(report["status"], status, report)
        return report

    def test_empty_log_is_inconclusive(self):
        report = self.assertStatus([], "INCONCLUSIVE")
        self.assertEqual(report["validEvents"], 0)
        self.assertEqual(report["markerLines"], 0)

    def test_false_marker_and_tool_citation_are_ignored(self):
        lines = [
            'tool output mentions [REENTRYDIAG] {"schema": 1}\n',
            "tool output: " + log_line(real_callsite_events()[0]).strip() + "\n",
            '[ReentryTrace] [DEBUG] [REENTRYDIAG] ' + json.dumps(real_callsite_events()[0]) + "\n",
        ]
        report = self.assertStatus(lines, "INCONCLUSIVE")
        self.assertEqual(report["markerLines"], 0)
        self.assertEqual(report["validEvents"], 0)

    def test_true_prefix_with_bad_json_is_invalid(self):
        self.assertStatus(["[ReentryTrace] [INFO] [REENTRYDIAG] {broken}\n"], "INVALID")

    def test_true_wrapped_prefix_is_accepted(self):
        report = analyze_lines([log_line(real_callsite_events()[0], wrapped=True)], COMMIT)
        self.assertEqual(report["validEvents"], 1)
        literal = "[[timestamp]] " + log_line(real_callsite_events()[0]).strip() + "\n"
        self.assertEqual(analyze_lines([literal], COMMIT)["validEvents"], 1)
        process = "2026-09-22 14:00:00.123 Minis[123:456] " + log_line(real_callsite_events()[0]).strip() + "\n"
        self.assertEqual(analyze_lines([process], COMMIT)["validEvents"], 1)

    def test_missing_chain_is_inconclusive(self):
        lines = [log_line(real_callsite_events()[0]), log_line(real_callsite_events()[2])]
        report = self.assertStatus(lines, "INCONCLUSIVE")
        self.assertIsNone(report["chain"]["target"])

    def test_real_callsite_shaped_chain_is_captured(self):
        report = self.assertStatus([log_line(value) for value in real_callsite_events()], "CAPTURED")
        self.assertEqual(report["validEvents"], 11)
        self.assertEqual(report["chain"]["completeTargetCount"], 1)
        self.assertEqual(report["chain"]["target"], {
            "owner": CV, "cell": CELL, "vm": VM, "idx": 7,
            "generation": 4, "session": SESSION,
        })

    def test_none_sessions_and_minus_one_indices_are_not_filled(self):
        report = self.assertStatus([log_line(value) for value in real_callsite_events()], "CAPTURED")
        self.assertEqual(report["chain"]["target"]["session"], SESSION)
        # These are the actual target shapes, not synthetic session/index values.
        self.assertEqual(real_callsite_events()[3]["session"], "none")
        self.assertEqual(real_callsite_events()[3]["idx"], -1)
        self.assertEqual(real_callsite_events()[4]["session"], "none")
        self.assertEqual(real_callsite_events()[4]["idx"], -1)

    def test_wrong_sha_is_invalid(self):
        bad = dict(real_callsite_events()[0])
        bad["commit"] = "f" * 40
        self.assertStatus([log_line(bad)], "INVALID")

    def test_duplicate_sequence_is_invalid(self):
        first, second = real_callsite_events()[0], dict(real_callsite_events()[1])
        second["seq"] = first["seq"]
        report = self.assertStatus([log_line(first), log_line(second)], "INVALID")
        self.assertGreater(report["errorCounts"]["sequence_duplicate"], 0)

    def test_out_of_order_sequence_is_invalid(self):
        first, second = real_callsite_events()[0], dict(real_callsite_events()[1])
        second["seq"] = 0
        self.assertStatus([log_line(first), log_line(second)], "INVALID")

    def test_sequence_gap_is_inconclusive(self):
        first, second = real_callsite_events()[0], dict(real_callsite_events()[1])
        second["seq"] = 3
        report = self.assertStatus([log_line(first), log_line(second)], "INCONCLUSIVE")
        self.assertGreater(report["errorCounts"]["sequence_gap"], 0)

    def test_limit_is_inconclusive(self):
        values = real_callsite_events()
        values[0] = event(1, "limit", owner=VM, session=SESSION)
        self.assertStatus([log_line(value) for value in values], "INCONCLUSIVE")

    def test_nonfinite_value_is_inconclusive(self):
        values = real_callsite_events()
        values[3] = dict(values[3])
        values[3]["values"] = {"width": "nonfinite"}
        self.assertStatus([log_line(value) for value in values], "INCONCLUSIVE")

    def test_io_error_sentinel_is_inconclusive(self):
        sentinel = {"schema": 1, "kind": "io-error"}
        report = self.assertStatus([log_line(sentinel)], "INCONCLUSIVE")
        self.assertEqual(report["errorCounts"]["io-error"], 1)

    def test_ambiguous_generation_is_inconclusive(self):
        values = real_callsite_events()
        extra = dict(values[4])
        extra["seq"] = 6
        extra["generation"] = 5
        for value in values[5:]:
            value["seq"] += 1
        values.insert(5, extra)
        self.assertStatus([log_line(value) for value in values], "INCONCLUSIVE")

    def test_height_roundtrip_uses_old_new_pairs_and_keeps_observation_only(self):
        report = self.assertStatus([log_line(value) for value in real_callsite_events()], "CAPTURED")
        observations = report["observations"]["layoutHeightShrinkGrow"]
        self.assertEqual(len(observations), 1)
        candidate = observations[0]
        self.assertEqual(candidate["owner"], CV)
        self.assertEqual(candidate["idx"], 7)
        self.assertEqual(candidate["left"]["oldH"], 1521.0)
        self.assertEqual(candidate["left"]["newH"], 1354.0)
        self.assertEqual(candidate["right"]["oldH"], 1354.0)
        self.assertEqual(candidate["right"]["newH"], 1521.0)
        self.assertEqual(candidate["after"][0]["values"]["contentH"], 2200.0)

    def test_no_target_records_cannot_be_green(self):
        values = [event(1, "mount", owner=VM, session=SESSION), event(2, "unmount", owner=VM, session=SESSION)]
        self.assertStatus([log_line(value) for value in values], "INCONCLUSIVE")

    def test_cli_writes_aggregate_report_without_source_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            log_path = tmp_path / "device.log"
            report_path = tmp_path / "report.json"
            secret = "PRIVATE_CHAT_TEXT_SHOULD_NOT_APPEAR"
            log_path.write_text("noise\n" + "".join(log_line(value) for value in real_callsite_events()) + secret + "\n", encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(HERE / "analyze_trace.py"), "log", str(log_path),
                 "--expected-commit", COMMIT, "--output", str(report_path)],
                text=True, capture_output=True, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            report_text = report_path.read_text(encoding="utf-8")
            self.assertNotIn(secret, report_text)
            self.assertEqual(json.loads(report_text)["status"], "CAPTURED")


if __name__ == "__main__":
    unittest.main(verbosity=2)
