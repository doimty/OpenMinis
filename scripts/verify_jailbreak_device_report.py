#!/usr/bin/env python3
"""Cross-check an exported iOS 15 device report and raw minis-mcp-cli receipts."""

import argparse
import json
import math
import re
import sys
import uuid
from datetime import datetime
from pathlib import Path


TOOLS = {"get_device_info", "get_screen_info", "get_brightness", "set_brightness"}
FAILURES = {"denied", "revoked", "expired", "unavailable"}
BUNDLE_ID = "com.openminis.app.root"
TOLERANCE = 0.02
MAX_INPUT_BYTES = 1024 * 1024
BRIGHTNESS_SEQUENCE = (
    ("get_brightness", "allowed"), ("set_brightness", "denied"),
    ("get_brightness", "allowed"), ("set_brightness", "allowed"),
    ("get_brightness", "allowed"), ("set_brightness", "allowed"),
    ("get_brightness", "allowed"),
)


class EvidenceError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise EvidenceError(message)


def strict_json(text):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, f"duplicate JSON field: {key}")
            result[key] = value
        return result

    def reject(constant):
        raise EvidenceError(f"invalid JSON number: {constant}")

    return json.loads(text, object_pairs_hook=pairs, parse_constant=reject)


def keys(value, required, label, optional=()):
    require(type(value) is dict and set(required) <= value.keys()
            and value.keys() <= set(required) | set(optional), f"unexpected {label} fields")


def number(value, minimum, maximum):
    return type(value) in (int, float) and minimum <= value <= maximum and math.isfinite(value)


def string_matches(value, expression):
    return type(value) is str and re.fullmatch(expression, value, re.ASCII) is not None


def check_timestamp(value):
    require(string_matches(value, r"\d{4}-\d{2}-\d{2}T\d\d:\d\d:\d\d(?:\.\d{1,6})?Z"),
            "recordedAt must be an ISO 8601 UTC timestamp")
    try:
        return datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as error:
        raise EvidenceError("invalid calendar timestamp") from error


def check_data(operation, data, os_version):
    if operation == "get_device_info":
        keys(data, {"model", "systemName", "systemVersion"}, "device info")
        require(data["systemName"] in ("iOS", "iPadOS") and data["systemVersion"] == os_version,
                "device info does not match the reported operating system")
        require(type(data["model"]) is str and 0 < len(data["model"]) <= 128
                and data["model"].strip() == data["model"] and "simulator" not in data["model"].lower(),
                "missing or simulator device model")
    elif operation == "get_screen_info":
        keys(data, {"widthPoints", "heightPoints", "scale"}, "screen info")
        require(all(number(data[key], 1, 32768) for key in ("widthPoints", "heightPoints"))
                and number(data["scale"], 1, 8), "invalid screen dimensions or scale")
    else:
        keys(data, {"brightness"}, "brightness result")
        require(number(data["brightness"], 0, 1), "brightness must be in [0, 1]")


def correlate(record, receipt, server, os_version):
    keys(receipt, {"server", "tool", "result"}, "MCP CLI call")
    require(receipt["server"] == server and receipt["tool"] == record["operation"],
            "receipt uses another server or operation")
    result = receipt["result"]
    require(type(result) is dict and type(result.get("isError")) is bool,
            "receipt lacks an explicit MCP isError result")
    succeeded = record["outcome"] == "allowed"
    require(result["isError"] is (not succeeded), "MCP status and native outcome disagree")
    observation = result.get("structuredContent")
    keys(observation, {"request_id", "operation", "ok", "data" if succeeded else "error"}, "structured receipt")
    require(type(observation["request_id"]) is str and observation["request_id"].lower() == record["requestID"].lower()
            and observation["operation"] == record["operation"],
            "MCP request ID or operation does not match native execution order")
    require(observation["ok"] is succeeded, "MCP ok and native outcome disagree")
    if succeeded:
        check_data(record["operation"], observation["data"], os_version)
        require(observation["data"] == record["data"], "MCP data and native readback disagree")
    else:
        require(observation["error"] == record["outcome"], "MCP error is not the recorded decision")
    content = result.get("content")
    require(type(content) is list and len(content) == 1, "expected one MCP text receipt")
    keys(content[0], {"type", "text"}, "MCP text block")
    require(content[0]["type"] == "text" and type(content[0]["text"]) is str, "invalid MCP text receipt")
    decoded = strict_json(content[0]["text"])
    require(json.dumps(decoded, allow_nan=False, sort_keys=True) == json.dumps(observation, allow_nan=False, sort_keys=True),
            "model-visible MCP text and structured receipt disagree")


def check_sequence(records):
    require(all(any(record["operation"] == operation and record["outcome"] == "allowed" for record in records)
                for operation in ("get_device_info", "get_screen_info")), "successful device and screen reads required")
    brightness = [record for record in records if record["operation"] in ("get_brightness", "set_brightness")]
    require(len(brightness) == len(BRIGHTNESS_SEQUENCE) and
            tuple((record["operation"], record["outcome"]) for record in brightness) == BRIGHTNESS_SEQUENCE,
            "brightness evidence must cover baseline, deny, unchanged read, allow, changed read, restore, final read")
    baseline, denied, unchanged, allowed, changed, restored, final = brightness
    initial = baseline["data"]["brightness"]
    target = allowed["requestedPercent"] / 100
    require(allowed["requestedPercent"] == denied["requestedPercent"], "deny and allow must cover the same target")
    require(abs(initial - target) >= 0.0999999, "choose a target at least ten percentage points from baseline")
    require(abs(initial - unchanged["data"]["brightness"]) <= TOLERANCE, "brightness changed after explicit denial")
    require(all(abs(target - record["data"]["brightness"]) <= TOLERANCE for record in (allowed, changed)),
            "changed brightness did not match the approved target")
    require(abs(restored["requestedPercent"] / 100 - initial) <= 0.0050001,
            "restore request must use the closest percentage to the original brightness")
    require(all(abs(initial - record["data"]["brightness"]) <= TOLERANCE for record in (restored, final)),
            "final brightness does not match the original brightness")


def check_candidate_manifest(manifest):
    if manifest.get("package") == BUNDLE_ID:
        require(manifest.get("source_signed_executables_verified") is True
                and manifest.get("native_floor_readback_verified") is True and manifest.get("deb_archive_readback_verified") is True
                and string_matches(manifest.get("deb_sha256"), r"[0-9a-f]{64}"), "incomplete candidate DEB manifest")
        return ("deb", manifest["deb_sha256"])
    require(manifest.get("package_floor") == "15.0" and manifest.get("signed") is True
            and manifest.get("extension_metadata_verified") is True and manifest.get("archive_executable_modes_verified") is True
            and string_matches(manifest.get("ipa_sha256"), r"[0-9a-f]{64}"), "incomplete or historical candidate IPA manifest")
    return ("ipa", manifest["ipa_sha256"])


def validate(manifest, report, receipts=None, *, expected_os=None):
    require(type(manifest) is dict and manifest.get("bundle_id") == BUNDLE_ID, "expected a Minis Root build manifest")
    commit = manifest.get("source_commit")
    run_id = manifest.get("actions_run_id")
    require(string_matches(commit, r"[0-9a-f]{40}") and string_matches(run_id, r"[1-9][0-9]*"),
            "invalid manifest commit or Actions run ID")
    require(manifest.get("minimum_os") == "15.0", "expected the 15.0 build manifest for this acceptance")
    artifact, digest = check_candidate_manifest(manifest)
    keys(report, {"schema", "kind", "bundleIdentifier", "build", "runtime", "osVersion", "records"}, "native report")
    require(type(report["schema"]) is int and report["schema"] == 1 and report["kind"] == "minis-device-mcp",
            "expected the native device MCP report, schema 1")
    require(report["runtime"] == "physical-device", "the native report must come from a physical device")
    keys(report["build"], {"commit", "actionsRunID"}, "native build")
    require(report["build"]["commit"] == commit and report["build"]["actionsRunID"] == run_id
            and report["bundleIdentifier"] == BUNDLE_ID, "native report does not match the supplied build")
    match = re.fullmatch(r"Version (15\.\d+(?:\.\d+)?) \(Build ([A-Za-z0-9.]+)\)",
                         report["osVersion"], re.ASCII) if isinstance(report["osVersion"], str) else None
    require(match is not None and (expected_os is None or report["osVersion"] == expected_os),
            "expected a matching iOS 15 physical-device OS/build report")
    records = report["records"]
    require(type(records) is list and 1 <= len(records) <= 64, "missing or oversized native records")
    previous = None
    seen = set()
    for record in records:
        keys(record, {"requestID", "operation", "outcome", "executed", "recordedAt"}, "native record",
             ("requestedPercent", "data"))
        request = record["requestID"]
        require(string_matches(request, r"[0-9A-Fa-f]{8}-(?:[0-9A-Fa-f]{4}-){3}[0-9A-Fa-f]{12}"),
                "requestID must be a canonical UUID")
        identifier = uuid.UUID(request)
        require(identifier.version == 4 and identifier not in seen, "duplicate or invalid request UUID")
        seen.add(identifier)
        operation = record["operation"]
        require(isinstance(operation, str) and operation in TOOLS, "unrecognized device operation")
        require(("requestedPercent" in record) == (operation == "set_brightness"), "unexpected brightness argument")
        if operation == "set_brightness":
            require(type(record["requestedPercent"]) is int and 0 <= record["requestedPercent"] <= 100,
                    "requested brightness must be an integer percentage")
        outcome = record["outcome"]
        require(isinstance(outcome, str) and outcome in FAILURES | {"allowed"}, "invalid outcome")
        executed = outcome == "allowed"
        require(record["executed"] is executed and ("data" in record) == executed,
                "native action, data and authorization decision disagree")
        if executed:
            check_data(operation, record["data"], match.group(1))
        timestamp = check_timestamp(record["recordedAt"])
        require(previous is None or previous <= timestamp, "native records are out of chronological order")
        previous = timestamp
    check_sequence(records)
    if receipts is not None:
        require(type(receipts) is list and len(receipts) == len(records) + 1, "one discovery and one receipt per call required")
        discovery = receipts[0]
        keys(discovery, {"server", "count", "tools"}, "MCP CLI discovery")
        require(discovery["server"] == "minis-device", "MCP discovery server mismatch")
        tools = discovery["tools"]
        require(type(tools) is list and type(discovery["count"]) is int and len(tools) == discovery["count"] == len(TOOLS),
                "unexpected number of advertised device MCP tools")
        require(all(type(tool) is dict and type(tool.get("name")) is str for tool in tools)
                and {tool["name"] for tool in tools} == TOOLS, "unexpected or missing device MCP tools")
        for record, receipt in zip(records, receipts[1:]):
            correlate(record, receipt, discovery["server"], match.group(1))
    return {"evidence": "native-plus-mcp" if receipts is not None else "native-only", "source_commit": commit,
            "actions_run_id": run_id, "bundle_id": BUNDLE_ID, "os": report["osVersion"], "matched_records": len(records),
            "reference_artifact": artifact, "reference_sha256": digest,
            "scope": "user-supplied UI authorization, device/screen reads, brightness denial, change and restoration",
            "root_execution": "not established by this report", "ios16_device_runtime": "not established by this report",
            "attestation": "user-captured evidence; local JSON records are not cryptographic device attestation"}


def limited_text(path):
    with path.open("rb") as stream:
        data = stream.read(MAX_INPUT_BYTES + 1)
    require(len(data) <= MAX_INPUT_BYTES, "evidence file too large")
    return data.decode("utf-8-sig")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True, help="trusted Actions 15.0 build manifest")
    parser.add_argument("--report", type=Path, required=True, help="JSON copied from the native Device MCP settings")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--receipts", type=Path, help="raw minis-mcp-cli normal-mode JSONL, discovery first then calls")
    mode.add_argument("--native-only", action="store_true", help="check native records without claiming MCP transport")
    parser.add_argument("--expected-os", help="expected full OS string, e.g. Version 15.1.1 (Build 19B81)")
    args = parser.parse_args(argv)
    try:
        receipts = ([strict_json(line) for line in limited_text(args.receipts).splitlines() if line.strip()]
                    if args.receipts else None)
        summary = validate(strict_json(limited_text(args.manifest)), strict_json(limited_text(args.report)),
                           receipts, expected_os=args.expected_os)
    except (OSError, UnicodeError, ValueError, RecursionError) as error:
        print(f"evidence rejected: {error}", file=sys.stderr)
        return 1
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
