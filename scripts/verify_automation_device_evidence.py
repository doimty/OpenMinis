#!/usr/bin/env python3
"""Correlate user-captured iOS 15 automation evidence; never attest a device or model vision."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
from pathlib import Path, PurePosixPath
import re
import sys
import uuid


SERVER = "minis-automation"
APP = "com.openminis.app.root"
COMPANION = "com.openminis.automation"
UPSTREAM = "62c4464f688c8f269419924b1a19c996de75f175"
TOOLS = {"get_device_info", "get_screen_info", "screenshot", "get_frontmost_app", "list_apps", "list_running_apps",
         "get_ui_elements", "get_element_at_point", "wait_for_element", "wait_for_disappear", "tap_screen",
         "tap_element", "swipe_screen", "long_press", "double_tap", "drag_and_drop", "input_text", "type_text",
         "press_key", "launch_app", "kill_app", "press_home"}
MAX_JSON = 16 * 1024 * 1024
MAX_REPORT = 1024 * 1024
MAX_IMAGE = 12 * 1024 * 1024
MAX_DEB = 512 * 1024 * 1024
IMAGE_SOURCE = Path(__file__).resolve().parents[1] / "src/ios/default_mount/usr/local/lib/minis-mcp-cli/automation_images.py"
IMAGE_SPEC = importlib.util.spec_from_file_location("automation_evidence_image_validation", IMAGE_SOURCE)
image_validation = importlib.util.module_from_spec(IMAGE_SPEC)
IMAGE_SPEC.loader.exec_module(image_validation)
STEP_TOOLS = {"device_info": {"get_device_info"}, "screen_info": {"get_screen_info"},
              "screen_before": {"screenshot"}, "launch": {"launch_app"}, "frontmost": {"get_frontmost_app"},
              "ui_before": {"get_ui_elements"}, "tap": {"tap_element", "tap_screen"},
              "input": {"input_text", "type_text"}, "ui_after": {"get_ui_elements"},
              "screen_after": {"screenshot"}, "expiry_baseline": {"get_screen_info"}}
ORDER = ("discovery", "device_info", "screen_info", "launch", "frontmost", "screen_before", "ui_before", "tap",
         "input", "ui_after", "screen_after", "stop_rejected", "expiry_discovery", "expiry_baseline", "expiry_rejected")


class EvidenceError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise EvidenceError(message)


def strict_json(text):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, "duplicate JSON field")
            result[key] = value
        return result

    def constant(_):
        raise EvidenceError("non-finite JSON number")

    return json.loads(text, object_pairs_hook=pairs, parse_constant=constant)


def fields(value, required, label, optional=()):
    require(type(value) is dict and set(required) <= value.keys() <= set(required) | set(optional),
            "unexpected " + label + " fields")


def text_matches(value, expression):
    return type(value) is str and re.fullmatch(expression, value, re.ASCII) is not None


def digest(value):
    return text_matches(value, r"[0-9a-f]{64}")


def identifier(value):
    if not text_matches(value, r"[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}"):
        return False
    return uuid.UUID(value).version == 4


def number(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def reject_secrets(value, depth=0):
    require(depth <= 48, "evidence nesting is too deep")
    if type(value) is dict:
        require(len(value) <= 4096, "evidence object is too large")
        for key, item in value.items():
            if key == "authorization" and item == "explicit-expiring-bearer-session":
                continue  # Public packaging policy, not an HTTP credential.
            require(key.casefold() not in {"token", "authorization", "headers", "bearer", "access_token"},
                    "credentials are not accepted in evidence")
            reject_secrets(item, depth + 1)
    elif type(value) is list:
        require(len(value) <= 4096, "evidence array is too large")
        for item in value:
            reject_secrets(item, depth + 1)
    elif type(value) is str:
        require(not re.search(r"\bbearer\s+[a-z0-9._~-]{8,}", value, re.I), "credentials are not accepted in evidence")
        require(len(value.encode("utf-8")) <= 2 * 1024 * 1024, "evidence string is too large")


def read_limited(path, limit):
    require(path.is_file() and not path.is_symlink(), "evidence input must be a regular file")
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    require(len(data) <= limit, "evidence input exceeds its size limit")
    return data


def file_hash(path, limit):
    require(path.is_file() and not path.is_symlink() and 0 < path.stat().st_size <= limit,
            "artifact file is absent, empty, unsafe or oversized")
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def manifests(main, companion):
    require(type(main) is dict and type(main.get("schema")) is int and main.get("schema") == 1 and main.get("package") == APP
            and main.get("bundle_id") == APP and main.get("minimum_os") == "15.0", "expected the iOS 15 main DEB manifest")
    commit, run = main.get("source_commit"), main.get("actions_run_id")
    require(text_matches(commit, r"[0-9a-f]{40}") and text_matches(run, r"[1-9][0-9]*"), "invalid build identity")
    scheme = main.get("scheme")
    architecture = {"rootless": "iphoneos-arm64", "roothide": "iphoneos-arm64e"}.get(scheme)
    require(architecture is not None and main.get("architecture") == architecture, "unexpected main package scheme")
    require(all(main.get(key) is True for key in ("source_signed_executables_verified", "source_signature_evidence_verified",
                "native_floor_readback_verified", "deb_archive_readback_verified")), "incomplete main packaging evidence")
    require(type(companion) is dict and type(companion.get("schema")) is int and companion.get("schema") == 1 and companion.get("package") == COMPANION
            and companion.get("scheme") == scheme and companion.get("architecture") == architecture
            and companion.get("minimum_os") == "15.0" and companion.get("source_commit") == commit
            and companion.get("actions_run_id") == run and companion.get("upstream_commit") == UPSTREAM,
            "companion and main build identities or schemes differ")
    require(companion.get("signature_verified") is True and companion.get("deb_archive_readback_verified") is True
            and companion.get("native_floors") == {"arm64": "15.0", "arm64e": "15.0"}
            and companion.get("default_enabled") is False and companion.get("bind_address") == "127.0.0.1"
            and companion.get("port") == 8091 and companion.get("authorization") == "explicit-expiring-bearer-session",
            "incomplete companion packaging evidence")
    require(all(digest(item.get("deb_sha256")) for item in (main, companion)), "missing package digest")
    return commit, run, scheme


def lifecycle(report, commit, run, expected_os):
    fields(report, {"schema", "kind", "bundleIdentifier", "build", "runtime", "osVersion", "appUID", "appEUID", "events"},
           "native report")
    fields(report["build"], {"commit", "actionsRunID"}, "native build")
    require(type(report["schema"]) is int and report["schema"] == 1 and report["kind"] == "minis-automation-session"
            and report["runtime"] == "physical-device" and report["bundleIdentifier"] == APP
            and report["build"] == {"commit": commit, "actionsRunID": run}, "native report does not match a physical installed build")
    require(text_matches(report["osVersion"], r"Version 15\.\d+(?:\.\d+)? \(Build [A-Za-z0-9.]+\)")
            and (expected_os is None or expected_os == report["osVersion"]), "expected the matching iOS 15 device OS/build")
    require(type(report["appUID"]) is int and type(report["appEUID"]) is int and report["appUID"] == report["appEUID"] == 501,
            "main app must report mobile identity")
    events = report["events"]
    require(type(events) is list and 1 <= len(events) <= 64, "missing native lifecycle events")
    sessions = {}
    previous = -1
    for event in events:
        fields(event, {"session_id", "event", "created", "expires", "observed", "tools_version"}, "lifecycle event")
        session = event["session_id"]
        require(identifier(session) and event["event"] in {"started", "stopped", "expired", "background-budget-ended", "invalidated"},
                "invalid native lifecycle event")
        require(type(event["tools_version"]) is int and event["tools_version"] == 1
                and all(number(event[key]) for key in ("created", "expires", "observed"))
                and abs(event["expires"] - event["created"] - 180) < 0.0001
                and event["observed"] >= event["created"] and event["observed"] >= previous,
                "invalid duration or lifecycle chronology")
        previous = event["observed"]
        history = sessions.setdefault(session, [])
        if event["event"] == "started":
            require(not history and event["observed"] < event["expires"], "duplicate or expired native grant")
        else:
            require(len(history) == 1 and history[0]["event"] == "started"
                    and all(event[key] == history[0][key] for key in ("created", "expires", "tools_version")),
                    "revocation does not match a single native grant")
            if event["event"] == "expired":
                require(event["observed"] >= event["expires"], "session expiry was recorded too early")
            elif event["event"] == "stopped":
                require(event["observed"] < event["expires"], "explicit stop occurred after expiry")
        history.append(event)
    return sessions


def provenance(result, commit, run, seen):
    require(type(result) is dict and result.get("isError") is False and type(result.get("content")) is list,
            "expected a successful MCP tool result")
    meta = result.get("_meta")
    require(type(meta) is dict and type(meta.get("openminis")) is dict, "missing companion provenance")
    source = meta["openminis"]
    fields(source, {"schema", "source_commit", "actions_run_id", "upstream_commit", "session_id", "request_id",
                    "generation", "runtime", "host_bundle", "uid", "euid", "authorized"}, "companion provenance")
    require(type(source["schema"]) is int and source["schema"] == 1 and source["source_commit"] == commit
            and source["actions_run_id"] == run and source["upstream_commit"] == UPSTREAM
            and source["runtime"] == "physical-device" and source["host_bundle"] == "com.apple.springboard"
            and source["authorized"] is True and identifier(source["session_id"]) and identifier(source["request_id"])
            and source["request_id"] not in seen and type(source["generation"]) is int and source["generation"] > 0
            and type(source["uid"]) is int and type(source["euid"]) is int and source["uid"] == source["euid"] == 501,
            "invalid, duplicate, unauthenticated or foreign companion provenance")
    seen.add(source["request_id"])
    return source


def structured(result):
    value = result.get("structuredContent")
    require(type(value) is dict, "missing independent structured observation")
    texts = [block.get("text") for block in result["content"] if type(block) is dict and block.get("type") == "text"]
    require(len(texts) == 1 and type(texts[0]) is str and strict_json(texts[0]) == value,
            "model-visible text and structured observation disagree")
    return value


def discovery(receipt):
    fields(receipt, {"server", "count", "tools"}, "tool discovery")
    require(receipt["server"] == SERVER and type(receipt["count"]) is int and receipt["count"] == 22
            and type(receipt["tools"]) is list and len(receipt["tools"]) == 22
            and all(type(tool) is dict and type(tool.get("name")) is str for tool in receipt["tools"])
            and {tool["name"] for tool in receipt["tools"]} == TOOLS, "unexpected automation tool discovery")


def rejection(receipt):
    fields(receipt, {"server", "error", "code"}, "client refusal")
    require(receipt["server"] == SERVER and receipt["code"] == "AUTOMATION_UNAVAILABLE"
            and type(receipt["error"]) is str and 0 < len(receipt["error"]) <= 1024,
            "expected an explicit client refusal after authorization ended")


def validate(main, companion, report, receipts, evidence, capture_root, *, expected_os=None,
             main_manifest_sha256, companion_manifest_sha256):
    for item in (main, companion, report, receipts, evidence):
        reject_secrets(item)
    commit, run, scheme = manifests(main, companion)
    sessions = lifecycle(report, commit, run, expected_os)
    fields(evidence, {"schema", "kind", "main_manifest_sha256", "companion_manifest_sha256", "source_commit", "actions_run_id",
                      "target_bundle_id", "public_marker", "primary_session_id", "expiry_session_id", "steps", "captures", "observations"},
           "capture index")
    require(type(evidence["schema"]) is int and evidence["schema"] == 1 and evidence["kind"] == "minis-automation-device-evidence"
            and evidence["source_commit"] == commit and evidence["actions_run_id"] == run
            and evidence["main_manifest_sha256"] == main_manifest_sha256
            and evidence["companion_manifest_sha256"] == companion_manifest_sha256, "capture index refers to another build or manifest")
    target, marker = evidence["target_bundle_id"], evidence["public_marker"]
    require(text_matches(target, r"[A-Za-z0-9][A-Za-z0-9.-]{2,254}") and target not in (APP, "com.openminis.app", "com.apple.springboard")
            and text_matches(marker, r"MINIS-AUTOMATION-[0-9a-f]{12,32}"), "expected a separate test app and a fresh public marker")
    primary, expiry = evidence["primary_session_id"], evidence["expiry_session_id"]
    require(identifier(primary) and identifier(expiry) and primary != expiry
            and len(sessions.get(primary, [])) == len(sessions.get(expiry, [])) == 2
            and sessions[primary][-1]["event"] == "stopped" and sessions[expiry][-1]["event"] == "expired"
            and sessions[primary][-1]["observed"] <= sessions[expiry][0]["observed"],
            "explicit-stop and natural-expiry grants are both required")
    require(type(receipts) is list and 15 <= len(receipts) <= 128, "unexpected receipt count")
    steps = evidence["steps"]
    fields(steps, set(ORDER), "step index")
    require(all(type(steps[name]) is int and 0 <= steps[name] < len(receipts) for name in ORDER)
            and all(steps[a] < steps[b] for a, b in zip(ORDER, ORDER[1:])), "step indices do not preserve execution order")
    require(steps["discovery"] == 0 and steps["expiry_rejected"] == len(receipts) - 1, "receipt boundary does not match acceptance")
    traces, seen = {}, set()
    grant_generations = {}
    for index, receipt in enumerate(receipts):
        if index in (steps["discovery"], steps["expiry_discovery"]):
            discovery(receipt)
            continue
        if index in (steps["stop_rejected"], steps["expiry_rejected"]):
            rejection(receipt)
            continue
        fields(receipt, {"server", "tool", "result"}, "tool receipt", ("captures",))
        require(receipt["server"] == SERVER and receipt["tool"] in TOOLS, "unknown automation receipt")
        trace = provenance(receipt["result"], commit, run, seen)
        wanted_session = primary if index < steps["stop_rejected"] else expiry
        require(trace["session_id"] == wanted_session, "tool chain crossed native grants")
        previous_generation = grant_generations.setdefault(wanted_session, trace["generation"])
        require(trace["generation"] == previous_generation, "tool chain crossed server generations")
        traces[index] = trace
    require(grant_generations[expiry] > grant_generations[primary], "expiry grant did not follow the primary server generation")
    for name, allowed in STEP_TOOLS.items():
        require(receipts[steps[name]]["tool"] in allowed, "acceptance step uses an unexpected tool")
    device = structured(receipts[steps["device_info"]]["result"])
    expected_version = re.search(r"Version ([0-9.]+)", report["osVersion"]).group(1)
    require(device.get("systemName") in ("iOS", "iPadOS") and device.get("systemVersion") == expected_version
            and type(device.get("machine")) is str and device["machine"].startswith(("iPhone", "iPad")), "device info and native OS differ")
    screen = structured(receipts[steps["screen_info"]]["result"])
    expiry_screen = structured(receipts[steps["expiry_baseline"]]["result"])
    for value in (screen, expiry_screen):
        require(all(number(value.get(key)) and 0 < value[key] <= 8192 for key in ("width", "height"))
                and number(value.get("scale")) and 1 <= value["scale"] <= 8, "invalid independent screen information")
    frontmost = structured(receipts[steps["frontmost"]]["result"])
    require(frontmost.get("bundleId") == target, "launch was not independently observed in the target app")
    before, after = (structured(receipts[steps[name]]["result"]) for name in ("ui_before", "ui_after"))
    require(before.get("bundleId") == after.get("bundleId") == target, "UI observations come from a different app")
    for tree in (before, after):
        require(type(tree.get("elements")) is list and 1 <= len(tree["elements"]) <= 2000
                and all(type(element) is dict for element in tree["elements"]), "missing independent UI elements")
    require(not any(marker in str(element.get("text", "")) for element in before["elements"])
            and any(type(element.get("text")) is str and marker in element["text"] for element in after["elements"]),
            "public marker must be absent before input and independently observed afterwards")
    captures = evidence["captures"]
    require(type(captures) is list and len(captures) == 2, "before and after screenshot files are required")
    screenshot_indices = {steps["screen_before"], steps["screen_after"]}
    observed_indices = set()
    capture_digests = {}
    for item in captures:
        fields(item, {"receipt_index", "local_file", "sha256"}, "exported capture")
        index, name = item["receipt_index"], item["local_file"]
        require(type(index) is int and index in screenshot_indices and index not in observed_indices and type(name) is str,
                "duplicate or unrelated capture index")
        observed_indices.add(index)
        relative = PurePosixPath(name)
        require(name and not relative.is_absolute() and relative.as_posix() == name and ".." not in relative.parts
                and "\\" not in name and ":" not in name and all(ord(char) >= 32 for char in name), "unsafe exported capture path")
        image_path = capture_root.joinpath(*relative.parts)
        root_resolved = capture_root.resolve()
        require(root_resolved in image_path.resolve().parents, "exported capture escapes the capture folder")
        record = receipts[index].get("captures")
        require(type(record) is list and len(record) == 1, "screenshot receipt lacks materialization evidence")
        capture = record[0]
        fields(capture, {"path", "sha256", "mimeType", "bytes", "width", "height"}, "screenshot materialization")
        require(digest(item["sha256"]) and item["sha256"] == capture["sha256"] == file_hash(image_path, MAX_IMAGE)
                and type(capture["bytes"]) is int and capture["bytes"] == image_path.stat().st_size
                and capture["mimeType"] in ("image/jpeg", "image/png") and type(capture["path"]) is str
                and PurePosixPath(capture["path"]).is_absolute() and "/.minis-automation-captures/" in capture["path"]
                and all(type(capture[key]) is int and 0 < capture[key] <= 8192 for key in ("width", "height")),
                "exported screenshot bytes differ from the original CLI capture")
        require((capture["width"], capture["height"]) in ((round(screen["width"]), round(screen["height"])),
                    (round(screen["height"]), round(screen["width"]))), "screenshot dimensions are not screen points")
        data = read_limited(image_path, MAX_IMAGE)
        require((capture["mimeType"] == "image/png" and data.startswith(b"\x89PNG\r\n\x1a\n"))
                or (capture["mimeType"] == "image/jpeg" and data.startswith(b"\xff\xd8") and data.endswith(b"\xff\xd9")),
                "exported screenshot type does not match its bytes")
        observed_dimensions = (image_validation.png_dimensions(data) if capture["mimeType"] == "image/png"
                               else image_validation.jpeg_dimensions(data))
        require(observed_dimensions == (capture["width"], capture["height"]), "capture dimensions differ from actual image bytes")
        capture_digests[index] = capture["sha256"]
    require(observed_indices == screenshot_indices, "missing a screenshot export")
    require(all(type(receipt.get("captures")) is list if receipt.get("tool") == "screenshot"
                else "captures" not in receipt for receipt in receipts if type(receipt) is dict and "tool" in receipt),
            "capture data attached to a non-screenshot tool")
    observations = evidence["observations"]
    require(type(observations) is list and len(observations) == 2, "real read_image observation references are required")
    observation_indices = set()
    for observation in observations:
        fields(observation, {"receipt_index", "request_id", "capture_sha256", "read_image_call_id", "observation"}, "model observation reference")
        index = observation["receipt_index"]
        require(type(index) is int and index in screenshot_indices and index not in observation_indices
                and observation["request_id"] == traces[index]["request_id"]
                and observation["capture_sha256"] == capture_digests[index]
                and type(observation["read_image_call_id"]) is str and 1 <= len(observation["read_image_call_id"]) <= 256
                and type(observation["observation"]) is str and 8 <= len(observation["observation"].strip()) <= 4096,
                "read_image reference does not match the exported screenshot")
        observation_indices.add(index)
    return {"evidence": "correlated-user-captured-automation", "source_commit": commit, "actions_run_id": run,
            "scheme": scheme, "os": report["osVersion"], "successful_tool_receipts": len(traces),
            "main_deb_sha256": main["deb_sha256"], "companion_deb_sha256": companion["deb_sha256"],
            "public_marker_readback": "independent target-app UI observation matched", "capture_files_matched": 2,
            "stop_and_expiry": "native lifecycle plus client refusal before transport; live server refusal not established",
            "model_vision": "read_image references supplied by operator; JSON validation cannot prove the model saw an image",
            "ios16_device_runtime": "not established", "attestation": "user-captured records; not cryptographic device attestation"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--main-manifest", type=Path, required=True)
    parser.add_argument("--main-deb", type=Path, required=True)
    parser.add_argument("--companion-manifest", type=Path, required=True)
    parser.add_argument("--companion-deb", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--receipts", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--capture-dir", type=Path, required=True)
    parser.add_argument("--expected-os")
    args = parser.parse_args(argv)
    try:
        main_bytes = read_limited(args.main_manifest, MAX_REPORT)
        companion_bytes = read_limited(args.companion_manifest, MAX_REPORT)
        main_manifest, companion_manifest = strict_json(main_bytes), strict_json(companion_bytes)
        require(main_manifest.get("deb_sha256") == file_hash(args.main_deb, MAX_DEB)
                and main_manifest.get("deb_bytes") == args.main_deb.stat().st_size
                and main_manifest.get("deb_name") == args.main_deb.name, "main artifact bytes do not match their manifest")
        require(companion_manifest.get("deb_sha256") == file_hash(args.companion_deb, MAX_DEB)
                and companion_manifest.get("deb_bytes") == args.companion_deb.stat().st_size
                and companion_manifest.get("deb_name") == args.companion_deb.name, "companion artifact bytes do not match their manifest")
        report = strict_json(read_limited(args.report, MAX_REPORT))
        receipts = [strict_json(line) for line in read_limited(args.receipts, MAX_JSON).splitlines() if line.strip()]
        evidence = strict_json(read_limited(args.evidence, MAX_REPORT))
        result = validate(main_manifest, companion_manifest, report, receipts, evidence, args.capture_dir,
                          expected_os=args.expected_os, main_manifest_sha256=hashlib.sha256(main_bytes).hexdigest(),
                          companion_manifest_sha256=hashlib.sha256(companion_bytes).hexdigest())
    except (OSError, ValueError, TypeError, AttributeError, KeyError, RecursionError, UnicodeError) as error:
        print("automation evidence rejected: " + str(error), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
