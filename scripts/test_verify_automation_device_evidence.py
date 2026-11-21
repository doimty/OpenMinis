#!/usr/bin/env python3
"""Adversarial offline fixtures only. These synthetic records are never device evidence."""
import hashlib
import io
import json
from pathlib import Path
import tempfile
import struct
import unittest
import uuid
import zlib
from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy

from verify_automation_device_evidence import APP, COMPANION, TOOLS, UPSTREAM, EvidenceError, main, strict_json, validate


def synthetic_png(width, height, red):
    def chunk(kind, payload):
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload) & 0xffffffff)
    rows = (b"\0" + bytes((red, 0, 0)) * width) * height
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


def fixture(root):
    commit, run = "a" * 40, "12345678"
    primary, expiry = str(uuid.uuid4()), str(uuid.uuid4())
    main_manifest = {"schema": 1, "package": APP, "bundle_id": APP, "minimum_os": "15.0", "scheme": "rootless",
                     "architecture": "iphoneos-arm64", "source_commit": commit, "actions_run_id": run,
                     "source_signed_executables_verified": True, "source_signature_evidence_verified": True,
                     "native_floor_readback_verified": True, "deb_archive_readback_verified": True,
                     "deb_sha256": "b" * 64}
    companion = {"schema": 1, "package": COMPANION, "minimum_os": "15.0", "scheme": "rootless",
                 "architecture": "iphoneos-arm64", "source_commit": commit, "actions_run_id": run,
                 "upstream_commit": UPSTREAM, "signature_verified": True, "deb_archive_readback_verified": True,
                 "native_floors": {"arm64": "15.0", "arm64e": "15.0"}, "default_enabled": False,
                 "bind_address": "127.0.0.1", "port": 8091, "authorization": "explicit-expiring-bearer-session",
                 "deb_sha256": "c" * 64}
    events = []
    for session, created, ended, reason in ((primary, 1000, 1050, "stopped"), (expiry, 1060, 1240, "expired")):
        base = {"session_id": session, "created": created, "expires": created + 180, "tools_version": 1}
        events += [dict(base, event="started", observed=created + .1), dict(base, event=reason, observed=ended)]
    report = {"schema": 1, "kind": "minis-automation-session", "bundleIdentifier": APP,
              "build": {"commit": commit, "actionsRunID": run}, "runtime": "physical-device",
              "osVersion": "Version 15.1.1 (Build 19B81)", "appUID": 501, "appEUID": 501, "events": events}
    discovery = {"server": "minis-automation", "count": 22, "tools": [{"name": name} for name in sorted(TOOLS)]}
    marker, target = "MINIS-AUTOMATION-123456789abc", "com.example.public-test"
    receipts = [deepcopy(discovery)]
    steps, captures, observations = {"discovery": 0}, [], []

    def call(step, tool, structured=None, session=primary):
        index = len(receipts)
        steps[step] = index
        trace = {"schema": 1, "source_commit": commit, "actions_run_id": run, "upstream_commit": UPSTREAM,
                 "session_id": session, "request_id": str(uuid.uuid4()), "generation": 1 if session == primary else 3,
                 "runtime": "physical-device", "host_bundle": "com.apple.springboard", "uid": 501, "euid": 501, "authorized": True}
        result = {"isError": False, "content": [{"type": "text", "text": "fixture success"}], "_meta": {"openminis": trace}}
        if structured is not None:
            result["structuredContent"] = structured
            result["content"] = [{"type": "text", "text": json.dumps(structured)}]
        receipts.append({"server": "minis-automation", "tool": tool, "result": result})
        if tool == "screenshot":
            # A generated solid-colour test image exercises image parsing and
            # hash correlation; it is never a physical-device screenshot.
            data = synthetic_png(375, 667, 0 if step == "screen_before" else 1)
            path = root / (step + ".png")
            path.write_bytes(data)
            digest = hashlib.sha256(data).hexdigest()
            capture = {"path": "/root/.minis-automation-captures/" + str(uuid.uuid4()) + ".png", "sha256": digest,
                       "mimeType": "image/png", "bytes": len(data), "width": 375, "height": 667}
            receipts[-1]["captures"] = [capture]
            captures.append({"receipt_index": index, "local_file": path.name, "sha256": digest})
            observations.append({"receipt_index": index, "request_id": trace["request_id"], "capture_sha256": digest,
                                 "read_image_call_id": "fixture-" + step, "observation": "Synthetic fixture observation; never a physical-device claim."})
        return index

    call("device_info", "get_device_info", {"systemName": "iOS", "systemVersion": "15.1.1", "machine": "iPhone8,1"})
    call("screen_info", "get_screen_info", {"width": 375, "height": 667, "scale": 2})
    call("launch", "launch_app")
    call("frontmost", "get_frontmost_app", {"bundleId": target})
    call("screen_before", "screenshot")
    call("ui_before", "get_ui_elements", {"bundleId": target, "elements": [{"text": "Public test input", "type": "textField"}]})
    call("tap", "tap_element")
    call("input", "input_text")
    call("ui_after", "get_ui_elements", {"bundleId": target, "elements": [{"text": marker, "type": "textField"}]})
    call("screen_after", "screenshot")
    refusal = {"server": "minis-automation", "code": "AUTOMATION_UNAVAILABLE", "error": "当前无有效授权。"}
    steps["stop_rejected"] = len(receipts)
    receipts.append(deepcopy(refusal))
    steps["expiry_discovery"] = len(receipts)
    receipts.append(deepcopy(discovery))
    call("expiry_baseline", "get_screen_info", {"width": 375, "height": 667, "scale": 2}, session=expiry)
    steps["expiry_rejected"] = len(receipts)
    receipts.append(deepcopy(refusal))
    evidence = {"schema": 1, "kind": "minis-automation-device-evidence", "source_commit": commit, "actions_run_id": run,
                "main_manifest_sha256": "d" * 64, "companion_manifest_sha256": "e" * 64, "target_bundle_id": target,
                "public_marker": marker, "primary_session_id": primary, "expiry_session_id": expiry,
                "steps": steps, "captures": captures, "observations": observations}
    return main_manifest, companion, report, receipts, evidence


class AutomationEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.data = fixture(self.root)

    def tearDown(self):
        self.temporary.cleanup()

    def check(self, data=None):
        return validate(*(data or self.data), self.root, main_manifest_sha256="d" * 64, companion_manifest_sha256="e" * 64)

    def test_correlated_chain_keeps_visual_and_live_server_limits_explicit(self):
        result = self.check()
        self.assertEqual(result["capture_files_matched"], 2)
        self.assertIn("cannot prove", result["model_vision"])
        self.assertIn("not established", result["stop_and_expiry"])
        self.assertEqual(result["ios16_device_runtime"], "not established")

    def test_wrong_build_runtime_schema_or_manifest_digest_fails(self):
        for index, key, value in ((1, "actions_run_id", "2"), (1, "source_commit", "f" * 40), (2, "runtime", "simulator"),
                                  (2, "osVersion", "Version 16.2 (Build 20C65)"), (2, "appUID", 0),
                                  (4, "main_manifest_sha256", "0" * 64), (4, "companion_manifest_sha256", "0" * 64)):
            data = deepcopy(self.data)
            data[index][key] = value
            with self.subTest(key=key), self.assertRaises(EvidenceError):
                self.check(data)

    def test_tokens_and_headers_are_refused_in_any_input(self):
        for secret in ({"token": "a" * 64}, {"Authorization": "Bearer " + "a" * 64}, {"note": "Bearer " + "b" * 64}):
            data = deepcopy(self.data)
            data[4]["secret"] = secret
            with self.subTest(secret=list(secret)), self.assertRaises(EvidenceError):
                self.check(data)

    def test_trace_ids_runtime_grant_and_generation_must_match(self):
        for key, value in (("request_id", None), ("runtime", "unknown"), ("authorized", False), ("source_commit", "f" * 40),
                           ("session_id", str(uuid.uuid4())), ("generation", 99), ("uid", 0)):
            data = deepcopy(self.data)
            trace = data[3][2]["result"]["_meta"]["openminis"]
            trace[key] = data[3][1]["result"]["_meta"]["openminis"]["request_id"] if key == "request_id" else value
            with self.subTest(key=key), self.assertRaises(EvidenceError):
                self.check(data)

    def test_marker_echo_wrong_app_or_preexisting_marker_is_not_independent_readback(self):
        for kind in ("echo-only", "other-app", "preexisting"):
            data = deepcopy(self.data)
            steps = data[4]["steps"]
            result = data[3][steps["ui_after"]]["result"]
            if kind == "echo-only":
                data[3][steps["ui_after"]]["tool"] = "input_text"
            elif kind == "other-app":
                result["structuredContent"]["bundleId"] = APP
                result["content"][0]["text"] = json.dumps(result["structuredContent"])
            else:
                before = data[3][steps["ui_before"]]["result"]
                before["structuredContent"]["elements"][0]["text"] = data[4]["public_marker"]
                before["content"][0]["text"] = json.dumps(before["structuredContent"])
            with self.subTest(kind=kind), self.assertRaises(EvidenceError):
                self.check(data)

    def test_model_visible_text_cannot_diverge_from_structured_ui(self):
        data = deepcopy(self.data)
        result = data[3][data[4]["steps"]["ui_after"]]["result"]
        result["content"][0]["text"] = "{}"
        with self.assertRaises(EvidenceError):
            self.check(data)

    def test_requires_natural_expiry_and_explicit_stop_not_budget_end(self):
        for event_index, event in ((1, "background-budget-ended"), (3, "stopped")):
            data = deepcopy(self.data)
            data[2]["events"][event_index]["event"] = event
            with self.subTest(event=event), self.assertRaises(EvidenceError):
                self.check(data)
        data = deepcopy(self.data)
        data[2]["events"][3]["observed"] = 1239
        with self.assertRaises(EvidenceError):
            self.check(data)

    def test_unknown_outcome_is_not_a_stop_or_expiry_refusal(self):
        data = deepcopy(self.data)
        data[3][data[4]["steps"]["stop_rejected"]]["code"] = "AUTOMATION_OUTCOME_UNKNOWN"
        with self.assertRaises(EvidenceError):
            self.check(data)

    def test_capture_substitution_and_unsafe_export_path_fail(self):
        for name in ("../outside.jpg", "/absolute.jpg", "C:/outside.jpg", "folder\\image.jpg"):
            data = deepcopy(self.data)
            data[4]["captures"][0]["local_file"] = name
            with self.subTest(name=name), self.assertRaises(EvidenceError):
                self.check(data)
        (self.root / "screen_after.png").write_bytes(b"changed")
        with self.assertRaises(EvidenceError):
            self.check()

    def test_missing_read_image_reference_is_refused_without_claiming_it_proves_vision(self):
        data = deepcopy(self.data)
        data[4]["observations"] = []
        with self.assertRaises(EvidenceError):
            self.check(data)

    def test_duplicate_json_nonfinite_and_chronology_fail(self):
        for text in ('{"schema":1,"schema":1}', '{"now":NaN}', '{"now":Infinity}'):
            with self.subTest(text=text), self.assertRaises(EvidenceError):
                strict_json(text)
        data = deepcopy(self.data)
        data[4]["steps"]["input"] = data[4]["steps"]["ui_after"]
        with self.assertRaises(EvidenceError):
            self.check(data)

    def test_cli_checks_actual_artifact_bytes_and_raw_manifest_hashes(self):
        main_manifest, companion, report, receipts, evidence = deepcopy(self.data)
        for manifest, name, content in ((main_manifest, "main.deb", b"synthetic-main-fixture"),
                                         (companion, "companion.deb", b"synthetic-companion-fixture")):
            path = self.root / name
            path.write_bytes(content)
            manifest.update(deb_name=name, deb_bytes=len(content), deb_sha256=hashlib.sha256(content).hexdigest())
        for name, value in (("main.json", main_manifest), ("companion.json", companion), ("report.json", report)):
            (self.root / name).write_text(json.dumps(value), encoding="utf-8")
        evidence["main_manifest_sha256"] = hashlib.sha256((self.root / "main.json").read_bytes()).hexdigest()
        evidence["companion_manifest_sha256"] = hashlib.sha256((self.root / "companion.json").read_bytes()).hexdigest()
        (self.root / "evidence.json").write_text(json.dumps(evidence), encoding="utf-8")
        (self.root / "receipts.jsonl").write_text("\n".join(json.dumps(value) for value in receipts), encoding="utf-8")
        arguments = ["--main-manifest", str(self.root / "main.json"), "--main-deb", str(self.root / "main.deb"),
                     "--companion-manifest", str(self.root / "companion.json"), "--companion-deb", str(self.root / "companion.deb"),
                     "--report", str(self.root / "report.json"), "--receipts", str(self.root / "receipts.jsonl"),
                     "--evidence", str(self.root / "evidence.json"), "--capture-dir", str(self.root)]
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self.assertEqual(main(arguments), 0)
            (self.root / "main.deb").write_bytes(b"different package")
            self.assertEqual(main(arguments), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
