#!/usr/bin/env python3
"""Validate the complete-app iOS 15 provisional-height device report.

The driver is observational only. This module ignores any driver verdict and
classifies the report from provenance, controls, markers, and the correlated
production trace.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
import uuid
from pathlib import Path
from typing import Any, Iterable

BASELINE_COMMIT = "2f21e242df71d63576682f8ac61c50a097f3a5ae"
CASES = (
    "initial",
    "deferred-reentry",
    "deferred-settle",
    "attachment-growth",
    "reuse",
    "plain-control",
)
CONTROL_CASES = ("initial", "attachment-growth", "reuse", "plain-control")
HEX40 = re.compile(r"^[0-9a-fA-F]{40}$")
HEX64 = re.compile(r"^[0-9a-fA-F]{64}$")
NONE_IDS = {"", "none", "null"}
BAD_KINDS = {"limit", "io-error", "encoding-error", "capture-error"}
BAD_SENTINELS = {"nonfinite", "non-finite", "NaN", "Infinity", "-Infinity"}
TOLERANCE = 2.0


def _finite(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _positive_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _nonnegative_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _near(left: Any, right: Any, tolerance: float = TOLERANCE) -> bool:
    return _finite(left) and _finite(right) and abs(float(left) - float(right)) <= tolerance


def _uuid(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        uuid.UUID(value)
    except (ValueError, AttributeError, TypeError):
        return False
    return True


def _id(value: Any) -> bool:
    return isinstance(value, str) and bool(value)


def _has_bad_sentinel(value: Any) -> bool:
    if isinstance(value, str):
        return value in BAD_SENTINELS
    if isinstance(value, dict):
        return any(_has_bad_sentinel(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_has_bad_sentinel(item) for item in value)
    return False


def _invalid(reason: str, evidence: Iterable[int] = ()) -> ValueError:
    evidence_text = ",".join(str(seq) for seq in evidence)
    suffix = f" evidenceSeq={evidence_text}" if evidence_text else ""
    return ValueError(f"{reason}{suffix}")


def _frame(value: Any, label: str) -> list[float]:
    if not isinstance(value, list) or len(value) != 4 or not all(_finite(item) for item in value):
        raise _invalid(f"{label} must be a finite four-number frame")
    return [float(item) for item in value]


def _height(event: dict[str, Any]) -> float | None:
    value = event.get("values", {}).get("height")
    return float(value) if _finite(value) else None


def _case_reference(case: dict[str, Any]) -> tuple[float, float, int]:
    reference = case.get("reference")
    if not isinstance(reference, dict):
        raise _invalid(f"case {case.get('name')}: missing reference")
    width, height, text_length = reference.get("width"), reference.get("height"), reference.get("textLength")
    if not _finite(width) or float(width) <= 1:
        raise _invalid(f"case {case.get('name')}: invalid reference width")
    if not _finite(height) or float(height) <= 1:
        raise _invalid(f"case {case.get('name')}: invalid reference height")
    if not _positive_int(text_length):
        raise _invalid(f"case {case.get('name')}: invalid reference textLength")
    return float(width), float(height), int(text_length)


def _validate_event(event: Any, index: int, report_run: str, report_commit: str) -> dict[str, Any]:
    if not isinstance(event, dict):
        raise _invalid(f"trace event {index} is not an object")
    required = ("schema", "run", "commit", "seq", "t", "main", "kind", "owner",
                "subject", "parent", "idx", "generation", "phase", "values")
    missing = [key for key in required if key not in event]
    if missing:
        raise _invalid(f"trace event {index} missing {','.join(missing)}")
    if event["schema"] != 1 or event["run"] != report_run or event["commit"] != report_commit:
        raise _invalid(f"trace event {index} provenance mismatch", [event.get("seq", -1)])
    if not _positive_int(event["seq"]):
        raise _invalid(f"trace event {index} has invalid seq")
    if not _finite(event["t"]) or float(event["t"]) < 0:
        raise _invalid(f"trace event {index} has invalid t", [event["seq"]])
    if type(event["main"]) is not bool or not isinstance(event["kind"], str):
        raise _invalid(f"trace event {index} has invalid type fields", [event["seq"]])
    if not all(_id(event[key]) for key in ("owner", "subject", "parent", "phase")):
        raise _invalid(f"trace event {index} has invalid identity fields", [event["seq"]])
    if not isinstance(event["idx"], int) or isinstance(event["idx"], bool):
        raise _invalid(f"trace event {index} has invalid idx", [event["seq"]])
    if not _nonnegative_int(event["generation"]):
        raise _invalid(f"trace event {index} has invalid generation", [event["seq"]])
    if not isinstance(event["values"], dict):
        raise _invalid(f"trace event {index} values is not an object", [event["seq"]])
    if event["kind"] in BAD_KINDS or _has_bad_sentinel(event["values"]):
        raise _invalid(f"trace event {index} contains {event['kind']} or a bad sentinel", [event["seq"]])
    return event


def _validate_case_shape(case: Any, expected_name: str) -> dict[str, Any]:
    if not isinstance(case, dict) or case.get("name") != expected_name:
        raise _invalid(f"missing or reordered case {expected_name}")
    digest = case.get("fixtureSHA256")
    if not isinstance(digest, str) or not HEX64.fullmatch(digest):
        raise _invalid(f"case {expected_name}: invalid fixtureSHA256")
    if not _id(case.get("collectionID")) or case["collectionID"] in NONE_IDS:
        raise _invalid(f"case {expected_name}: invalid collectionID")
    start, end = case.get("startSeq"), case.get("endSeq")
    if not _positive_int(start) or not _positive_int(end) or start > end:
        raise _invalid(f"case {expected_name}: invalid marker sequence range")
    reference_width, reference_height, reference_text_length = _case_reference(case)
    snapshot = case.get("snapshot")
    if not isinstance(snapshot, dict):
        raise _invalid(f"case {expected_name}: missing snapshot")
    cell_frame = _frame(snapshot.get("cellFrame"), f"case {expected_name} cellFrame")
    host_frame = _frame(snapshot.get("hostFrame"), f"case {expected_name} hostFrame")
    text_bounds = _frame(snapshot.get("textBounds"), f"case {expected_name} textBounds")
    if not _positive_int(snapshot.get("textLength")):
        raise _invalid(f"case {expected_name}: invalid snapshot textLength")
    if snapshot["textLength"] != reference_text_length:
        raise _invalid(f"case {expected_name}: reference/subject textLength mismatch")
    if snapshot.get("inWindow") is not True:
        raise _invalid(f"case {expected_name}: empty, hidden, or off-window content")
    if abs(text_bounds[2] - reference_width) > TOLERANCE:
        raise _invalid(f"case {expected_name}: reference/textBounds width mismatch")
    return {
        "case": case,
        "name": expected_name,
        "fixture": digest.lower(),
        "collection": case["collectionID"],
        "start": int(start),
        "end": int(end),
        "reference_width": reference_width,
        "reference_height": reference_height,
        "reference_text_length": reference_text_length,
        "cell_height": cell_frame[3],
        "host_height": host_frame[3],
    }


def _validate_markers(trace: list[dict[str, Any]], cases: dict[str, dict[str, Any]]) -> None:
    for name, info in cases.items():
        markers = [event for event in trace if event["kind"] == "probe-marker" and event["phase"] == name]
        if len(markers) != 2:
            raise _invalid(f"case {name}: expected exactly two probe markers", [event["seq"] for event in markers])
        if markers[0]["seq"] != info["start"] or markers[1]["seq"] != info["end"]:
            raise _invalid(f"case {name}: case range does not match marker seq", [event["seq"] for event in markers])
        edges = [markers[0]["values"].get("edge"), markers[1]["values"].get("edge")]
        if any(isinstance(edge, bool) or not _finite(edge) for edge in edges) or edges != [0, 1]:
            raise _invalid(f"case {name}: invalid marker edges", [event["seq"] for event in markers])
        if markers[0]["owner"] != info["collection"] or markers[1]["owner"] != info["collection"]:
            raise _invalid(f"case {name}: marker collection mismatch", [event["seq"] for event in markers])


def _window_events(trace: list[dict[str, Any]], info: dict[str, Any]) -> list[dict[str, Any]]:
    return [event for event in trace if info["start"] <= event["seq"] <= info["end"]]


def _same_fixture_window(cases: dict[str, dict[str, Any]]) -> None:
    if cases["deferred-reentry"]["fixture"] != cases["initial"]["fixture"]:
        raise _invalid("deferred-reentry does not use initial fixture")
    if cases["deferred-settle"]["fixture"] != cases["deferred-reentry"]["fixture"]:
        raise _invalid("deferred-settle fixture differs from reentry fixture")
    if cases["deferred-reentry"]["collection"] != cases["deferred-settle"]["collection"]:
        raise _invalid("deferred reentry/settle collection differs")
    if cases["attachment-growth"]["fixture"] == cases["initial"]["fixture"]:
        raise _invalid("attachment-growth fixture did not change")
    if cases["reuse"]["fixture"] != cases["attachment-growth"]["fixture"]:
        raise _invalid("reuse fixture is not the growth fixture")
    if cases["plain-control"]["fixture"] in {cases["initial"]["fixture"], cases["attachment-growth"]["fixture"]}:
        raise _invalid("plain-control fixture collides with Markdown fixture")


def _control_status(cases: dict[str, dict[str, Any]]) -> str | None:
    failures = []
    for name in CONTROL_CASES:
        info = cases[name]
        if not _near(info["cell_height"], info["reference_height"]) or not _near(info["host_height"], info["reference_height"]):
            failures.append(name)
    return ",".join(failures) if failures else None


def _same_size(left: dict[str, Any], right: dict[str, Any]) -> bool:
    return all(_near(left["values"].get(key), right["values"].get(key), 0.001)
               for key in ("width", "height"))


def _transition_chain(events: list[dict[str, Any]], layout: dict[str, Any],
                      target_height: float, minimum_seq: int) -> dict[str, Any] | None:
    """Require the real configure/preference/delivery/accept/return/write path.

    Cell generations and host-root generations are separate namespaces. A
    preference alone is not delivery, and cell acceptance has idx=-1 in the
    actual production trace; configure plus cell-return supplies the row.
    """
    owner, idx, end = layout["owner"], layout["idx"], layout["seq"]
    returns = [e for e in events if e["kind"] == "cell-return" and e["phase"] == "legacy-observation"
               and e["owner"] == owner and e["idx"] == idx
               and minimum_seq < e["seq"] < end and _near(_height(e), target_height, 0.001)]
    for returned in reversed(returns):
        cell = returned["subject"]
        configs = [e for e in events if e["kind"] == "configure" and e["owner"] == owner
                   and e["subject"] == cell and e["seq"] < returned["seq"]]
        if not configs:
            continue
        config = configs[-1]
        if config["idx"] != idx or config["generation"] != returned["generation"]:
            continue
        accepts = [e for e in events if e["kind"] == "host-size" and e["phase"] == "cell-accepted"
                   and e["owner"] == owner and e["subject"] == cell and e["idx"] == -1
                   and e["generation"] == returned["generation"]
                   and max(minimum_seq, config["seq"]) < e["seq"] < returned["seq"]
                   and _height(e) is not None and _near(math.ceil(_height(e)), target_height, 0.001)]
        for accept in reversed(accepts):
            deliveries = [e for e in events if e["kind"] == "host-size" and e["phase"] == "delivered"
                          and e["owner"] == owner and e["parent"] == cell and e["idx"] == -1
                          and max(minimum_seq, config["seq"]) < e["seq"] < accept["seq"]
                          and _same_size(e, accept)]
            for delivery in reversed(deliveries):
                preferences = [e for e in events if e["kind"] == "host-size" and e["phase"] == "preference"
                               and e["owner"] == owner and e["subject"] == delivery["subject"]
                               and e["parent"] == cell and e["idx"] == -1
                               and e["generation"] == delivery["generation"]
                               and max(minimum_seq, config["seq"]) < e["seq"] < delivery["seq"]
                               and _same_size(e, delivery)]
                if preferences:
                    preference = preferences[-1]
                    return {"cell": cell, "generation": returned["generation"], "config": config,
                            "preference": preference,
                            "seqs": [config["seq"], preference["seq"], delivery["seq"],
                                     accept["seq"], returned["seq"], end]}
    return None


def _find_reentry(cases: dict[str, dict[str, Any]], trace: list[dict[str, Any]]) -> tuple[list[int] | None, str]:
    target = cases["deferred-reentry"]
    combined = _window_events(trace, target) + _window_events(trace, cases["deferred-settle"])
    combined = {event["seq"]: event for event in combined}
    events = [combined[seq] for seq in sorted(combined)]
    # This driver creates exactly one subject row, index 0.
    layouts = [e for e in events if e["kind"] == "layout-height"
               and e["owner"] == target["collection"] and e["idx"] == 0]
    for drop in layouts:
        old_height, new_height = drop["values"].get("oldH"), drop["values"].get("newH")
        if (not _finite(old_height) or not _finite(new_height) or float(new_height) <= 0
                or float(new_height) >= float(old_height) - TOLERANCE
                or not _near(old_height, target["reference_height"])):
            continue
        lower = _transition_chain(events, drop, float(new_height), target["start"])
        if lower is None:
            continue
        provisional = [e for e in events if e["kind"] == "text-size" and e["phase"] == "intrinsic-unset"
                       and e["owner"] == target["collection"] and e["parent"] == lower["cell"]
                       and e["generation"] == lower["generation"] and e["idx"] == -1
                       and lower["config"]["seq"] < e["seq"] < lower["preference"]["seq"]
                       and _finite(e["values"].get("boundsW")) and e["values"]["boundsW"] <= 1]
        if not provisional:
            continue
        for recover in layouts:
            if recover["idx"] != drop["idx"] or recover["seq"] <= drop["seq"]:
                continue
            values = recover["values"]
            if not _near(values.get("oldH"), new_height, 0.001) or not _near(values.get("newH"), old_height, 0.001):
                continue
            upper = _transition_chain(events, recover, float(old_height), drop["seq"])
            if upper is None:
                continue
            for unset in provisional:
                finite = [e for e in events if e["kind"] == "text-size"
                          and e["phase"] in {"intrinsic-finite", "correction-measure"}
                          and e["owner"] == unset["owner"] and e["subject"] == unset["subject"]
                          and e["parent"] == unset["parent"] and e["generation"] == unset["generation"]
                          and unset["seq"] < e["seq"] < recover["seq"]
                          and _near(e["values"].get("width"), target["reference_width"])
                          and _near(e["values"].get("height"), target["reference_height"])]
                if finite:
                    seqs = sorted(set(lower["seqs"] + upper["seqs"] + [unset["seq"], finite[0]["seq"]]))
                    return seqs, "same-row provisional-text/host-delivery/cell/cache shrink and finite-width recovery"
    return None, "no complete provisional-text to same-row shrink/recovery chain"


def validate_report(data: dict[str, Any], expected_commit: str | None = None) -> dict[str, Any]:
    """Return status/reason/evidenceSeq, or raise ValueError for INVALID input."""
    if not isinstance(data, dict):
        raise _invalid("report is not an object")
    if data.get("schema") != 1 or data.get("kind") != "minis-provisional-height-device-probe":
        raise _invalid("schema or kind mismatch")
    run_id = data.get("runID")
    if not _uuid(run_id):
        raise _invalid("runID is not a UUID")
    source_commit = data.get("sourceCommit")
    if not isinstance(source_commit, str) or not HEX40.fullmatch(source_commit):
        raise _invalid("sourceCommit is not a 40-hex build commit")
    if expected_commit is not None and source_commit.lower() != expected_commit.lower():
        raise _invalid("sourceCommit does not match --commit")
    if data.get("baselineCommit") != BASELINE_COMMIT:
        raise _invalid("baselineCommit mismatch")
    if not isinstance(data.get("os"), str) or not data["os"] or not data["os"].split(".", 1)[0] == "15":
        raise _invalid("report is not from an iOS 15 device")
    if data.get("legacyPath") is not True:
        raise _invalid("legacyPath is not true")
    if "captureError" in data:
        raise _invalid(f"native captureError: {data.get('captureError')}")

    raw_cases = data.get("cases")
    if not isinstance(raw_cases, list) or [case.get("name") if isinstance(case, dict) else None for case in raw_cases] != list(CASES):
        raise _invalid("cases are missing, reordered, or duplicated")
    cases = {name: _validate_case_shape(case, name) for case, name in zip(raw_cases, CASES)}
    requires_fresh = data.get("requiresFreshReentry", False)
    if type(requires_fresh) is not bool:
        raise _invalid("requiresFreshReentry must be a Boolean")
    if requires_fresh:
        initial = cases["initial"]["case"]["snapshot"]
        reentry = cases["deferred-reentry"]["case"]["snapshot"]
        for key in ("cellID", "textID"):
            if not all(_id(s.get(key)) and s[key] not in NONE_IDS for s in (initial, reentry)):
                raise _invalid(f"fresh reentry lacks {key} evidence")
            if initial[key] == reentry[key]:
                raise _invalid(f"fresh reentry reused the already measured {key}")

    trace = data.get("trace")
    if not isinstance(trace, list) or not trace:
        raise _invalid("trace is missing or empty")
    events = [_validate_event(event, index, run_id, source_commit) for index, event in enumerate(trace)]
    seqs = [event["seq"] for event in events]
    if seqs != list(range(1, len(events) + 1)):
        raise _invalid("complete trace sequence must be contiguous from 1")
    times = [float(event["t"]) for event in events]
    if times != sorted(times):
        raise _invalid("trace clock moved backwards")
    _validate_markers(events, cases)
    _same_fixture_window(cases)

    control_failure = _control_status(cases)
    if control_failure:
        raise _invalid(f"control final frame differs from independent reference: {control_failure}")

    evidence, reason = _find_reentry(cases, events)
    if evidence:
        return {"status": "BASELINE_REPRODUCED", "reason": reason, "evidenceSeq": evidence}
    return {"status": "INCONCLUSIVE", "reason": reason, "evidenceSeq": []}


def _cli() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("report", type=Path)
    parser.add_argument("--commit")
    args = parser.parse_args()
    try:
        result = validate_report(json.loads(args.report.read_text()), expected_commit=args.commit)
    except (OSError, json.JSONDecodeError, ValueError) as error:
        print(f"status=INVALID reason={error}")
        return 2
    evidence = ",".join(str(seq) for seq in result["evidenceSeq"]) or "none"
    print(f"status={result['status']} reason={result['reason']} evidenceSeq={evidence}")
    return 0 if result["status"] == "BASELINE_REPRODUCED" else 1


if __name__ == "__main__":
    raise SystemExit(_cli())
