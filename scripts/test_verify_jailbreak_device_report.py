#!/usr/bin/env python3
"""Behavioral fixtures for evidence correlations; these are not device reports."""

import io
import importlib.util
import json
import subprocess
import tempfile
import unittest
import uuid
from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
from pathlib import Path

from verify_jailbreak_device_report import BUNDLE_ID, EvidenceError, main, strict_json, validate

DRIVER = Path(__file__).resolve().parents[1] / "src/ios/default_mount/usr/local/lib/minis-mcp-cli/device_acceptance.py"
SPEC = importlib.util.spec_from_file_location("device_acceptance", DRIVER)
driver = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(driver)


def observations():
    server = "minis-device"
    commit = "a5" * 20
    run = "12345678"
    manifest = {"bundle_id": BUNDLE_ID, "source_commit": commit, "actions_run_id": run, "minimum_os": "15.0",
                "package_floor": "15.0", "signed": True, "extension_metadata_verified": True,
                "archive_executable_modes_verified": True, "ipa_sha256": "bc" * 32}
    records = []
    receipts = [{"server": server, "count": 4,
                 "tools": [{"name": tool, "inputSchema": {"type": "object"}} for tool in
                           ("get_device_info", "get_screen_info", "get_brightness", "set_brightness")]}]
    actions = [
        ("get_device_info", {"model": "iPhone", "systemName": "iOS", "systemVersion": "15.1.1"}, None),
        ("get_screen_info", {"widthPoints": 375.0, "heightPoints": 667.0, "scale": 2}, None),
        ("get_brightness", {"brightness": 0.733}, None),
        ("set_brightness", None, 48),
        ("get_brightness", {"brightness": 0.733}, None),
        ("set_brightness", {"brightness": 0.4801}, 48),
        ("get_brightness", {"brightness": 0.4801}, None),
        ("set_brightness", {"brightness": 0.7301}, 73),
        ("get_brightness", {"brightness": 0.7301}, None),
    ]
    for index, (tool, data, percent) in enumerate(actions):
        request_id = str(uuid.uuid4())
        allowed = data is not None
        outcome = "allowed" if allowed else "denied"
        record = {"requestID": request_id.upper(), "operation": tool, "outcome": outcome, "executed": allowed,
                  "recordedAt": f"2026-11-21T06:18:{index:02d}Z"}
        echo = {"request_id": request_id, "operation": tool, "ok": allowed}
        if percent is not None:
            record["requestedPercent"] = percent
        if allowed:
            record["data"] = dict(data)
            echo["data"] = dict(data)
        else:
            echo["error"] = outcome
        records.append(record)
        receipts.append({"server": server, "tool": tool,
                         "result": {"content": [{"type": "text", "text": json.dumps(echo)}],
                                    "structuredContent": echo, "isError": not allowed}})
    report = {"schema": 1, "kind": "minis-device-mcp", "bundleIdentifier": BUNDLE_ID,
              "runtime": "physical-device", "osVersion": "Version 15.1.1 (Build 19B81)",
              "build": {"commit": commit, "actionsRunID": run}, "records": records}
    return manifest, report, receipts


class DeviceReportTests(unittest.TestCase):
    def test_full_and_native_only_have_distinct_verdicts(self):
        manifest, report, receipts = observations()
        self.assertEqual(validate(manifest, report, receipts)["evidence"], "native-plus-mcp")
        result = validate(manifest, report)
        self.assertEqual(result["evidence"], "native-only")
        self.assertEqual(result["matched_records"], 9)
        self.assertIn("not established", result["ios16_device_runtime"])
        self.assertIn("not established", result["root_execution"])

    def test_deb_manifest_preserves_actual_reference_digest(self):
        manifest, report, receipts = observations()
        deb = {**manifest, "package": BUNDLE_ID, "source_signed_executables_verified": True,
               "native_floor_readback_verified": True, "deb_archive_readback_verified": True, "deb_sha256": "bd" * 32}
        result = validate(deb, report, receipts)
        self.assertEqual((result["reference_artifact"], result["reference_sha256"]), ("deb", "bd" * 32))
        for flag in ("source_signed_executables_verified", "native_floor_readback_verified", "deb_archive_readback_verified"):
            with self.subTest(flag=flag), self.assertRaises(EvidenceError):
                validate({**deb, flag: False}, report, receipts)

    def test_historical_helper_or_another_build_cannot_pass(self):
        manifest, report, receipts = observations()
        historical = {"schema": 1, "build": {"schema": 1, "commit": "2bb94a2c353ec1b162aafa0cf18a839842cec50f",
                                             "actionsRunID": "44872743232"}, "bundleIdentifier": BUNDLE_ID,
                      "osVersion": report["osVersion"], "helper": {"euid": 0, "uid": 0, "operation": "identity"}}
        for invalid in (historical, dict(report, build={**report["build"], "actionsRunID": "1"}),
                        dict(report, build={**report["build"], "commit": "2b" * 20}),
                        dict(report, bundleIdentifier="com.openminis.app"),
                        dict(report, osVersion="Version 16.2 (Build 20C65)"), dict(report, runtime="device"),
                        dict(report, runtime="simulator"),
                        dict(report, runtime="ios-app-on-mac")):
            with self.subTest(invalid=invalid.keys()), self.assertRaises(EvidenceError):
                validate(manifest, invalid, receipts)
        with self.assertRaises(EvidenceError):
            validate(manifest, report, receipts, expected_os="Version 15.0 (Build 19A346)")
        for key, value in (("minimum_os", "16.0"), ("package_floor", None), ("archive_executable_modes_verified", 1),
                           ("bundle_id", "com.openminis.app"), ("actions_run_id", 12345678)):
            with self.subTest(key=key), self.assertRaises(EvidenceError):
                validate({**manifest, key: value}, report, receipts)

    def test_only_explicit_denial_and_unchanged_readback_count(self):
        manifest, report, receipts = observations()
        for mutation in (dict(outcome="revoked"), dict(outcome="expired"), dict(executed=True),
                         dict(data={"brightness": 0.48}), dict(requestedPercent=49)):
            invalid = deepcopy(report)
            invalid["records"][3].update(mutation)
            with self.subTest(mutation=mutation), self.assertRaises(EvidenceError):
                validate(manifest, invalid)
        report["records"][4]["data"]["brightness"] = 0.48
        with self.assertRaisesRegex(EvidenceError, "changed after explicit denial"):
            validate(manifest, report, receipts)

    def test_an_independent_read_is_required_after_change_and_restore(self):
        manifest, report, _receipts = observations()
        for index, mutation in ((6, {"data": {"brightness": 0.733}}), (7, {"requestedPercent": 80}),
                                (8, {"data": {"brightness": 0.48}})):
            invalid = deepcopy(report)
            invalid["records"][index].update(mutation)
            with self.subTest(index=index), self.assertRaises(EvidenceError):
                validate(manifest, invalid)
        del report["records"][6]
        with self.assertRaises(EvidenceError):
            validate(manifest, report)

    def test_duplicate_ids_type_confusion_and_reordering_rejected(self):
        manifest, report, receipts = observations()
        for index, mutation in ((3, {"requestID": report["records"][2]["requestID"].lower()}),
                                (1, {"recordedAt": "2025-11-21T06:18:43Z"}),
                                (1, {"recordedAt": "2026-02-30T06:18:43Z"}),
                                (5, {"requestedPercent": True}), (5, {"executed": 1}),
                                (0, {"data": {"model": "iPhone Simulator", "systemName": "iOS", "systemVersion": "15.1.1"}}),
                                (1, {"data": {"widthPoints": True, "heightPoints": 200, "scale": 1}})):
            invalid = deepcopy(report)
            invalid["records"][index].update(mutation)
            with self.subTest(mutation=mutation), self.assertRaises(EvidenceError):
                validate(manifest, invalid)
        receipts[1], receipts[2] = receipts[2], receipts[1]
        with self.assertRaises(EvidenceError):
            validate(manifest, report, receipts)

    def test_tool_discovery_and_failed_result_must_match_native_report(self):
        manifest, report, receipts = observations()
        for field, value in (("tools", [{"name": "run_command"}] * 4), ("server", "ios-mcp"), ("count", True)):
            invalid = deepcopy(receipts)
            invalid[0][field] = value
            with self.subTest(field=field), self.assertRaises(EvidenceError):
                validate(manifest, report, invalid)
        for mutation in ({"error": "expired"}, {"ok": 0}, {"data": {}}, {"request_id": str(uuid.uuid4())}):
            invalid = deepcopy(receipts)
            invalid[4]["result"]["structuredContent"].update(mutation)
            with self.subTest(mutation=mutation), self.assertRaises(EvidenceError):
                validate(manifest, report, invalid)
        receipts[7]["result"]["structuredContent"]["data"]["brightness"] = 0.9
        with self.assertRaisesRegex(EvidenceError, "native readback disagree"):
            validate(manifest, report, receipts)

    def test_reject_duplicate_json_and_nonfinite_constants(self):
        for text in ('{"ok":true,"ok":false}', '{"value":NaN}', '{"value":Infinity}'):
            with self.subTest(text=text), self.assertRaises(EvidenceError):
                strict_json(text)

    def test_model_visible_text_cannot_misrepresent_an_authorized_action(self):
        manifest, report, receipts = observations()
        structured = receipts[4]["result"]["structuredContent"]
        receipts[4]["result"]["content"][0]["text"] = json.dumps({**structured, "ok": True})
        with self.assertRaisesRegex(EvidenceError, "model-visible MCP text"):
            validate(manifest, report, receipts)

    def test_cli_accepts_actual_unwrapped_shape_not_another_transport(self):
        manifest, report, receipts = observations()
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            manifest_file = folder / "manifest.json"
            report_file = folder / "device.json"
            receipts_file = folder / "cli.jsonl"
            manifest_file.write_text(json.dumps(manifest), encoding="utf-8")
            report_file.write_text(json.dumps(report), encoding="utf-8")
            args = ["--manifest", str(manifest_file), "--report", str(report_file), "--receipts", str(receipts_file)]
            for wrap, expected in ((False, 0), (True, 1)):
                rows = ({"ok": True, "result": item} if wrap else item for item in receipts)
                receipts_file.write_text("\n".join(json.dumps(item) for item in rows), encoding="utf-8")
                with self.subTest(wrap=wrap), redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                    self.assertEqual(main(args), expected)


class DriverTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.output = Path(temporary.name) / "my receipts.jsonl"
        self.operations = []
        self.native = []
        self.messages = []
        self.brightness = 0.52
        self.replace = {}

    def emulator(self, command):
        self.assertEqual(command[0], "/usr/local/bin/minis-mcp-cli")
        self.assertEqual(command[2], "minis-device")
        if command[1] == "refresh":
            listing = observations()[2][0]
            setting = next(tool for tool in listing["tools"] if tool["name"] == "set_brightness")
            setting["inputSchema"] = {"type": "object", "required": ["percent"],
                                      "properties": {"percent": {"type": "integer", "minimum": 0, "maximum": 100}}}
            return subprocess.CompletedProcess(command, 0, json.dumps(listing).encode() + b"\n")
        self.assertEqual(command[1], "call")
        operation = command[3]
        arguments = json.loads(command[4]) if len(command) == 5 else {}
        self.operations.append((operation, arguments))
        step = len(self.operations)
        request_id = str(uuid.uuid4())
        allowed = step != 4
        outcome = "allowed" if allowed else "denied"
        payload = {"request_id": request_id, "operation": operation, "ok": allowed}
        record = {"requestID": request_id, "operation": operation, "executed": allowed, "outcome": outcome,
                  "recordedAt": f"2026-11-21T06:26:{step:02d}Z"}
        if operation == "set_brightness":
            self.assertEqual(type(arguments["percent"]), int)
            record["requestedPercent"] = arguments["percent"]
            if allowed:
                self.brightness = arguments["percent"] / 100
        if allowed:
            data = {"get_device_info": {"model": "iPhone", "systemName": "iOS", "systemVersion": "15.1.1"},
                    "get_screen_info": {"widthPoints": 375, "heightPoints": 667, "scale": 2}}.get(operation, {"brightness": self.brightness})
            payload["data"] = data
            record["data"] = data
        else:
            payload["error"] = "denied"
        self.native.append(record)
        payload.update(self.replace.get(step, {}))
        response = {"server": "minis-device", "tool": operation,
                    "result": {"structuredContent": payload, "content": [{"type": "text", "text": json.dumps(payload)}],
                               "isError": not payload["ok"]}}
        return subprocess.CompletedProcess(command, 0, json.dumps(response).encode() + b"\n")

    def test_raw_capture_matches_offline_checker_and_restores_original(self):
        driver.capture(self.output, runner=self.emulator, inform=self.messages.append)
        self.assertEqual(len(self.operations), 9)
        writes = [args for operation, args in self.operations if operation == "set_brightness"]
        self.assertEqual(writes, [{"percent": 30}, {"percent": 30}, {"percent": 52}])
        self.assertEqual(self.brightness, 0.52)
        manifest, report, _ = observations()
        report["records"] = self.native
        captured = [strict_json(row) for row in self.output.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(validate(manifest, report, captured)["evidence"], "native-plus-mcp")

    def test_wrong_click_stops_retains_partial_output_and_prints_restore(self):
        self.replace[4] = {"ok": True, "data": {"brightness": 0.3}}
        with self.assertRaises(driver.CaptureError):
            driver.capture(self.output, runner=self.emulator, inform=self.messages.append)
        self.assertEqual(len(self.operations), 4)
        self.assertEqual(len(self.output.read_text(encoding="utf-8").splitlines()), 5)
        self.assertIn("52%", self.messages[-1])
        self.assertIn("手动恢复", self.messages[-1])

    def test_output_must_be_new_before_any_device_calls(self):
        self.output.write_bytes(b"previous observation")
        with self.assertRaises(FileExistsError):
            driver.capture(self.output, runner=self.emulator, inform=self.messages.append)
        self.assertEqual(self.operations, [])
        self.assertEqual(self.output.read_bytes(), b"previous observation")


if __name__ == "__main__":
    unittest.main()
