#!/usr/bin/env python3
"""Validate and aggregate ReentryDiagnostics log lines.

The analyzer is observational only. It never controls a device and never
copies source-log or chat content into the report.
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
import json
import math
from pathlib import Path
import re
import sys
import uuid
from typing import Any, Iterable, TextIO

TOKEN = "[ReentryTrace] [INFO] [REENTRYDIAG] "
# Dedicated files use TOKEN at column zero. Exporters may add one of these
# bounded timestamp/process prefixes; arbitrary prose before TOKEN is ignored.
WRAPPED_PREFIX = re.compile(
    r"^(?:\[\[(?:timestamp|\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:\.\d+)?|\d+(?:\.\d+)?)\]\]\s*"
    r"|\[\d{2}:\d{2}:\d{2}(?:\.\d+)?\]\s+\d{4}-\d{2}-\d{2}\s+"
    r"\d{2}:\d{2}:\d{2}(?:\.\d+)?\s+[A-Za-z0-9_.-]+\[\d+:\d+\]\s+"
    r"|\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}(?:\.\d+)?\s+[A-Za-z0-9_.-]+\[\d+:\d+\]\s+)"
)
COMMIT = re.compile(r"^[0-9a-fA-F]{40}$")
OBJECT_ID = re.compile(r"^(?:none|-?[0-9a-fA-F]+)$")
SESSION_ID = re.compile(r"^(?:none|[0-9a-fA-F]{8})$")
LABEL = re.compile(r"^[A-Za-z0-9._:()\-]{1,80}$")

KINDS = frozenset({
    "mount", "unmount", "root-unmount", "configure", "host-size",
    "cell-return", "text-size", "layout-height", "precalc", "prepare",
    "snapshot", "force-emission", "scroll-request", "viewport", "user-drag",
    "settle", "limit", "encoding-error", "io-error",
})
SENTINELS = frozenset({"encoding-error", "io-error"})
COORDINATOR_KINDS = frozenset({
    "mount", "unmount", "root-unmount", "viewport", "force-emission",
})


@dataclass(frozen=True)
class Event:
    data: dict[str, Any]
    line_number: int


def _reject_constant(value: str) -> None:
    raise ValueError(value)


def _json_object(payload: str) -> dict[str, Any]:
    value = json.loads(payload, parse_constant=_reject_constant)
    if not isinstance(value, dict):
        raise ValueError("root_not_object")
    return value


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _finite_number(value: Any) -> bool:
    return _is_number(value) and math.isfinite(float(value))


def _uuid(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        uuid.UUID(value)
    except (ValueError, AttributeError):
        return False
    return True


def _valid_event(data: dict[str, Any]) -> tuple[bool, str | None, bool]:
    required = {
        "schema", "run", "commit", "seq", "t", "main", "kind", "owner",
        "subject", "parent", "idx", "generation", "phase", "session", "values",
    }
    if not required.issubset(data):
        return False, "missing_field", False
    if type(data["schema"]) is not int or data["schema"] != 1:
        return False, "schema", False
    if not _uuid(data["run"]):
        return False, "run", False
    if not isinstance(data["commit"], str) or not COMMIT.fullmatch(data["commit"]):
        return False, "commit", False
    if type(data["seq"]) is not int or data["seq"] <= 0:
        return False, "seq", False
    if not _finite_number(data["t"]):
        return False, "time", False
    if type(data["main"]) is not bool:
        return False, "main", False
    if not isinstance(data["kind"], str) or data["kind"] not in KINDS:
        return False, "kind", False
    for field in ("owner", "subject", "parent"):
        if not isinstance(data[field], str) or not OBJECT_ID.fullmatch(data[field]):
            return False, field, False
    if type(data["idx"]) is not int or data["idx"] < -1:
        return False, "idx", False
    if type(data["generation"]) is not int or data["generation"] < 0:
        return False, "generation", False
    if not isinstance(data["phase"], str) or not LABEL.fullmatch(data["phase"]):
        return False, "phase", False
    if not isinstance(data["session"], str) or not SESSION_ID.fullmatch(data["session"]):
        return False, "session", False
    values = data["values"]
    if not isinstance(values, dict):
        return False, "values", False
    nonfinite = False
    for key, value in values.items():
        if not isinstance(key, str) or not LABEL.fullmatch(key):
            return False, "value_key", False
        if value == "nonfinite":
            nonfinite = True
        elif not _finite_number(value):
            return False, "value", False
    return True, None, nonfinite


def _extract(line: str) -> tuple[str | None, bool]:
    """Return (JSON payload, malformed_real_prefix).

    A line is a real candidate only at column zero or after a strict exporter
    prefix. Tool prose containing the marker is therefore ignored, while a
    timestamp/process line whose JSON is truncated is counted as malformed.
    """
    line = line.rstrip("\r\n")
    if line.startswith(TOKEN):
        return line[len(TOKEN):], True
    if (line.startswith("[[")
            or re.match(r"^\[\d{2}:\d{2}:\d{2}", line)
            or re.match(r"^\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}", line)):
        match = WRAPPED_PREFIX.match(line)
        if match and line.startswith(TOKEN, match.end()):
            return line[match.end() + len(TOKEN):], True
        if TOKEN in line:
            return None, True
    return None, False


def parse_log(lines: Iterable[str]) -> tuple[list[Event], Counter[str], int]:
    events: list[Event] = []
    errors: Counter[str] = Counter()
    marker_lines = 0
    for line_number, line in enumerate(lines, 1):
        payload, candidate = _extract(line)
        if not candidate:
            continue
        marker_lines += 1
        if payload is None:
            errors["prefix"] += 1
            continue
        try:
            data = _json_object(payload)
        except (json.JSONDecodeError, ValueError):
            errors["json"] += 1
            continue
        if data.get("schema") == 1 and data.get("kind") in SENTINELS:
            errors[str(data["kind"])] += 1
            continue
        valid, error, nonfinite = _valid_event(data)
        if not valid:
            errors[error or "schema"] += 1
            continue
        if nonfinite:
            errors["nonfinite"] += 1
        events.append(Event(data=data, line_number=line_number))
    return events, errors, marker_lines


def _event_order_errors(events: list[Event]) -> Counter[str]:
    errors: Counter[str] = Counter()
    if not events:
        return errors
    seen: set[int] = set()
    previous = 0
    for event in events:
        seq = event.data["seq"]
        if seq in seen:
            errors["sequence_duplicate"] += 1
        if seq <= previous:
            errors["sequence_order"] += 1
        seen.add(seq)
        previous = seq
    if min(seen) != 1 or len(seen) != max(seen):
        errors["sequence_gap"] += 1
    return errors


def _provenance_errors(events: list[Event], expected_commit: str) -> Counter[str]:
    errors: Counter[str] = Counter()
    if not COMMIT.fullmatch(expected_commit or ""):
        errors["expected_commit"] += 1
        return errors
    if not events:
        return errors
    if len({event.data["run"] for event in events}) != 1:
        errors["run_mismatch"] += 1
    if {event.data["commit"].lower() for event in events} != {expected_commit.lower()}:
        errors["commit_mismatch"] += 1
    return errors


def _value(data: dict[str, Any], *names: str) -> float | None:
    for name in names:
        value = data["values"].get(name)
        if _finite_number(value):
            return float(value)
    return None


def _height_pair(events: list[Event]) -> tuple[Event, Event] | None:
    layouts = [
        event for event in events
        if event.data["kind"] == "layout-height"
        and event.data["idx"] >= 0
        and _value(event.data, "oldH") is not None
        and _value(event.data, "newH") is not None
    ]
    for left_index, left in enumerate(layouts):
        old_left = _value(left.data, "oldH")
        new_left = _value(left.data, "newH")
        for right in layouts[left_index + 1:]:
            if left.data["owner"] != right.data["owner"] or left.data["idx"] != right.data["idx"]:
                continue
            if (old_left == _value(right.data, "newH")
                    and new_left == _value(right.data, "oldH")):
                return left, right
    return None


def _matching_anchor(event: Event, cv: str, vm: str) -> bool:
    data = event.data
    if data["kind"] in {"mount", "unmount", "root-unmount", "force-emission"}:
        return data["owner"] == vm
    if data["kind"] == "viewport":
        return data["owner"] == cv and data["subject"] == vm
    return False


def _segment_for_config(events: list[Event], config: Event) -> dict[str, Any] | None:
    c = config.data
    cv, cell, vm, idx, generation = c["owner"], c["subject"], c["parent"], c["idx"], c["generation"]
    if idx < 0 or cv == "none" or cell == "none" or vm == "none" or generation == 0:
        return None
    configs = [
        event for event in events
        if event.data["kind"] == "configure"
        and event.data["owner"] == cv
        and event.data["subject"] == cell
        and event.data["idx"] == idx
        and event.data["seq"] > c["seq"]
    ]
    next_seq = min((event.data["seq"] for event in configs), default=10**18)
    segment = [event for event in events if c["seq"] <= event.data["seq"] < next_seq]
    text = [
        event for event in segment
        if event.data["kind"] == "text-size"
        and event.data["owner"] == cv and event.data["parent"] == cell
        and event.data["idx"] == -1 and event.data["generation"] == generation
    ]
    host = [
        event for event in segment
        if event.data["kind"] == "host-size"
        and event.data["owner"] == cv and event.data["parent"] == cell
        and event.data["idx"] == -1 and event.data["generation"] > 0
    ]
    cell_return = [
        event for event in segment
        if event.data["kind"] == "cell-return"
        and event.data["owner"] == cv and event.data["subject"] == cell
        and event.data["idx"] == idx and event.data["generation"] == generation
    ]
    layout = [
        event for event in segment
        if event.data["kind"] == "layout-height"
        and event.data["owner"] == cv and event.data["idx"] == idx
    ]
    if not text or not host or not cell_return or not layout:
        return None
    text_subjects = {event.data["subject"] for event in text}
    host_subjects = {event.data["subject"] for event in host}
    host_parents = {event.data["parent"] for event in host}
    if len(text_subjects) != 1 or "none" in text_subjects:
        return None
    if len(host_subjects) != 1 or "none" in host_subjects or host_parents != {cell}:
        return None
    host_generations = {event.data["generation"] for event in host}
    if len(host_generations) != 1:
        return None
    pair = _height_pair(layout)
    if pair is None:
        return None

    mounts = [
        event for event in events
        if event.data["kind"] == "mount" and event.data["seq"] <= c["seq"]
        and _matching_anchor(event, cv, vm)
    ]
    if not mounts:
        return None
    start = max(mounts, key=lambda event: event.data["seq"])
    unmounts = [
        event for event in events
        if event.data["kind"] in {"unmount", "root-unmount"}
        and event.data["seq"] >= c["seq"]
        and _matching_anchor(event, cv, vm)
    ]
    if not unmounts:
        return None
    end = min(unmounts, key=lambda event: event.data["seq"])
    if end.data["seq"] < start.data["seq"]:
        return None
    sessions = {
        event.data["session"] for event in events
        if start.data["seq"] <= event.data["seq"] <= end.data["seq"]
        and _matching_anchor(event, cv, vm) and event.data["session"] != "none"
    }
    if len(sessions) != 1:
        return None
    return {
        "cv": cv, "cell": cell, "vm": vm, "idx": idx,
        "generation": generation, "session": next(iter(sessions)),
        "config": config, "text": text, "host": host,
        "cellReturn": cell_return, "layout": layout, "pair": pair,
    }


def _find_targets(events: list[Event]) -> tuple[list[dict[str, Any]], bool]:
    configs = [event for event in events if event.data["kind"] == "configure"]
    targets: list[dict[str, Any]] = []
    for config in configs:
        candidate = _segment_for_config(events, config)
        if candidate is not None:
            targets.append(candidate)
    # Multiple generations for the same anonymous cell are not safely
    # collapsible into one target, even when one generation lacks a later
    # callback. The real re-entry trace must remain conservative.
    generation_groups: dict[tuple[str, str, str, int], set[int]] = {}
    for config in configs:
        data = config.data
        if data["idx"] >= 0 and data["owner"] != "none" and data["subject"] != "none":
            key = (data["owner"], data["subject"], data["parent"], data["idx"])
            generation_groups.setdefault(key, set()).add(data["generation"])
    generation_ambiguous = any(len(generations) > 1 for generations in generation_groups.values())
    keys = {(item["cv"], item["cell"], item["idx"], item["generation"], item["session"]) for item in targets}
    ambiguous = generation_ambiguous or len(keys) > 1
    return targets, ambiguous


def _observations(events: list[Event]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, int], list[Event]] = {}
    for event in events:
        data = event.data
        if data["kind"] == "layout-height" and data["idx"] >= 0:
            groups.setdefault((data["owner"], data["idx"]), []).append(event)
    result: list[dict[str, Any]] = []
    for (owner, idx), group in groups.items():
        pair = _height_pair(sorted(group, key=lambda event: event.data["seq"]))
        if pair is None:
            continue
        left, right = pair
        after: list[dict[str, Any]] = []
        for event in events:
            if event.data["seq"] <= right.data["seq"] or event.data["kind"] not in {"viewport", "prepare", "layout-height"}:
                continue
            values: dict[str, float] = {}
            for key in ("contentH", "contentHeight", "viewportH", "offset", "oldH", "newH"):
                value = _value(event.data, key)
                if value is not None:
                    values[key] = value
            if values:
                after.append({"seq": event.data["seq"], "kind": event.data["kind"], "values": values})
        result.append({
            "owner": owner, "idx": idx,
            "left": {"seq": left.data["seq"], "oldH": _value(left.data, "oldH"), "newH": _value(left.data, "newH")},
            "right": {"seq": right.data["seq"], "oldH": _value(right.data, "oldH"), "newH": _value(right.data, "newH")},
            "after": after[:16],
        })
    return result[:64]


def analyze_lines(lines: Iterable[str], expected_commit: str) -> dict[str, Any]:
    events, parse_errors, marker_lines = parse_log(lines)
    errors = Counter(parse_errors)
    errors.update(_event_order_errors(events))
    errors.update(_provenance_errors(events, expected_commit))
    counts = Counter(event.data["kind"] for event in events)
    targets, ambiguous = _find_targets(events)
    invalid_codes = {
        "prefix", "json", "missing_field", "schema", "run", "commit", "seq", "time", "main",
        "kind", "owner", "subject", "parent", "idx", "generation", "phase", "session",
        "values", "value_key", "value", "expected_commit", "commit_mismatch", "run_mismatch",
        "sequence_duplicate", "sequence_order",
    }
    inconclusive_codes = {"sequence_gap", "nonfinite", "encoding-error", "io-error"}
    if errors.keys() & invalid_codes:
        status = "INVALID"
    elif not events or errors.keys() & inconclusive_codes or "limit" in counts or ambiguous or not targets:
        status = "INCONCLUSIVE"
    else:
        status = "CAPTURED"
    target = None
    if len(targets) == 1:
        target = {
            "owner": targets[0]["cv"], "cell": targets[0]["cell"],
            "vm": targets[0]["vm"], "idx": targets[0]["idx"],
            "generation": targets[0]["generation"], "session": targets[0]["session"],
        }
    return {
        "status": status,
        "expectedCommit": expected_commit.lower(),
        "schema": 1,
        "markerLines": marker_lines,
        "validEvents": len(events),
        "eventCounts": dict(sorted(counts.items())),
        "runCount": len({event.data["run"] for event in events}),
        "sequence": {"first": min((event.data["seq"] for event in events), default=None),
                      "last": max((event.data["seq"] for event in events), default=None)},
        "chain": {"target": target, "completeTargetCount": len(targets), "ambiguous": ambiguous},
        "observations": {"layoutHeightShrinkGrow": _observations(events)},
        "errorCounts": dict(sorted(errors.items())),
    }


def _read_input(path: str) -> TextIO:
    return sys.stdin if path == "-" else open(path, "r", encoding="utf-8", errors="replace")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    log_parser = subparsers.add_parser("log", help="analyze a ReentryTrace log")
    log_parser.add_argument("logfile", nargs="?", default="-")
    log_parser.add_argument("--input", dest="input_path")
    log_parser.add_argument("--expected-commit", required=True)
    log_parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    stream = _read_input(args.input_path or args.logfile)
    try:
        report = analyze_lines(stream, args.expected_commit)
    finally:
        if stream is not sys.stdin:
            stream.close()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"CAPTURED": 0, "INCONCLUSIVE": 1, "INVALID": 2}[report["status"]]


if __name__ == "__main__":
    raise SystemExit(main())
