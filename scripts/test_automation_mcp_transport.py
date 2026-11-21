#!/usr/bin/env python3
"""Real local HTTP and image fixtures; no iOS compiler or device involved.

Requires httpx==0.28.1. POSIX image storage checks run on Actions Linux/macOS;
Windows exercises fail-closed behavior because directory descriptors differ.
"""
from __future__ import annotations

import base64
import contextlib
import copy
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import io
import json
import os
from pathlib import Path
import socket
import stat
import struct
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import uuid
import zlib


ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "src/ios/default_mount/usr/local/lib/minis-mcp-cli"
sys.path.insert(0, str(LIB))
from transport import http as transport
from utils import config
import automation_images as images
import daemon

SPEC = importlib.util.spec_from_file_location("automation_cli_main", LIB / "main.py")
cli = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cli)

TOKEN = "ab" * 32
OTHER_TOKEN = "cd" * 32
SESSION = "12345678-1234-4234-8234-123456789abc"
OTHER_SESSION = "87654321-4321-4321-8321-cba987654321"
CAN_STORE = hasattr(os, "O_DIRECTORY") and hasattr(os, "O_NOFOLLOW") and os.open in os.supports_dir_fd


def native_config(token=TOKEN, session=SESSION):
    return {"enabled": True, "url": config.AUTOMATION_URL,
            "headers": {"Authorization": "Bearer " + token}, "session": session}


def png(width=2, height=3):
    def chunk(kind, payload):
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload) & 0xffffffff)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress((b"\0" + b"\xff\0\0\xff" * width) * height)) + chunk(b"IEND", b""))


def screenshot(payload=None, mime="image/png"):
    encoded = base64.b64encode(png() if payload is None else payload).decode("ascii")
    return {"server": "minis-automation", "tool": "screenshot", "result": {
        "isError": False, "content": [{"type": "image", "mimeType": mime, "data": encoded}],
        "structuredContent": {"untrusted_path": "/etc/not-selected.png", "echo": encoded}}}


@contextlib.contextmanager
def current_directory(path):
    previous = os.getcwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


class NativeConfigurationTests(unittest.TestCase):
    def read_bridge(self, payload, returncode=0):
        raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        with patch.object(config.os.path, "isfile", return_value=True), patch.object(config.subprocess, "run", return_value=
                subprocess.CompletedProcess([], returncode, raw, b"ignored")) as run:
            result = config.local_automation_config()
            run.assert_called_once_with(["/usr/local/bin/minis-automation-config"], stdout=subprocess.PIPE,
                                        stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, timeout=2, check=False)
            return result

    def test_native_schema_has_only_fixed_url_header_and_uuid(self):
        self.assertEqual(self.read_bridge(native_config()), native_config())
        self.assertIsNone(self.read_bridge({"enabled": False}))
        invalid = [dict(native_config(), url="http://example.invalid/mcp"),
                   dict(native_config(), url="http://127.0.0.1:8091/mcp/"),
                   dict(native_config(), session="not-a-uuid"),
                   dict(native_config(), session=str(uuid.uuid1())),
                   dict(native_config(), sessionUUID=SESSION),
                   dict(native_config(), enabled=1),
                   dict(native_config(), oauth={"token": TOKEN}),
                   dict(native_config(), headers={"Authorization": "Bearer " + TOKEN, "extra": "x"}),
                   dict(native_config(), headers={"authorization": "Bearer " + TOKEN}),
                   dict(native_config(), headers={"Authorization": "Bearer " + TOKEN.upper()}),
                   {"enabled": False, "headers": {"Authorization": "Bearer " + TOKEN}}]
        for value in invalid:
            with self.subTest(value=repr(value).replace(TOKEN, "<token>")):
                self.assertIsNone(self.read_bridge(value))
        self.assertIsNone(self.read_bridge(b'{"enabled":false,"enabled":true}'))
        self.assertIsNone(self.read_bridge(b"NaN"))
        self.assertIsNone(self.read_bridge(b"x" * 2049))
        self.assertIsNone(self.read_bridge(native_config(), returncode=1))
        with patch.object(config.os.path, "isfile", return_value=True), patch.object(config.subprocess, "run",
                side_effect=subprocess.TimeoutExpired("bridge", 2)):
            self.assertIsNone(config.local_automation_config())

    def test_all_case_variants_are_reserved_on_read_write_and_display(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(config, "CONFIG_DIR", temp), \
                patch.object(config, "CONFIG_PATH", str(Path(temp) / "servers.json")), \
                patch.object(config, "_local_device_state", return_value=False), \
                patch.object(config, "local_automation_config", return_value=native_config()):
            for name in ("minis-device", "MINIS-DEVICE", "Minis-Automation", "MINIS-AUTOMATION"):
                self.assertFalse(config.upsert_server(name, {"url": "https://example.invalid/forged"}))
                self.assertFalse(config.remove_server(name))
                self.assertFalse(config.set_enabled(name, True))
            config.save_config({"mcpServers": {"ordinary": {"url": "https://example.invalid/mcp"},
                                              "MINIS-AUTOMATION": native_config(),
                                              "MiNiS-DeViCe": {"command": "/bin/forged"}}})
            stored = Path(config.CONFIG_PATH).read_text()
            self.assertNotIn(TOKEN, stored)
            self.assertEqual(set(json.loads(stored)["mcpServers"]), {"ordinary"})
            self.assertEqual(config.get_server("MINIS-AUTOMATION"), native_config())
            self.assertNotIn(TOKEN, json.dumps(config.public_server_config("MINIS-AUTOMATION", native_config())))

    def test_existing_forged_file_is_filtered_without_native_grant(self):
        with patch.object(config, "load_config", return_value={"mcpServers": {
                "MINIS-AUTOMATION": {"url": "https://example.invalid/forged"},
                "Minis-Device": {"command": "/bin/forged"}, "ordinary": {"command": "/bin/example"}}}), \
                patch.object(config, "_local_device_state", return_value=False), \
                patch.object(config, "local_automation_config", return_value=None):
            self.assertEqual(set(config.get_servers()), {"ordinary"})


class LocalHTTPFixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if transport.httpx is None:
            raise RuntimeError("Install httpx==0.28.1 to run the real HTTP automation tests")

    def setUp(self):
        self.events, self.mode, self.cfg = [], "normal", None
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                method = body["method"]
                fixture.events.append((method, self.headers.get("Authorization"), body))
                if fixture.mode == "call_disconnect" and method == "tools/call":
                    self.connection.shutdown(socket.SHUT_RDWR)
                    self.connection.close()
                    return
                status, result, error = 200, {}, None
                if self.headers.get("Authorization") != fixture.cfg["headers"]["Authorization"]:
                    status = 401
                elif fixture.mode in ("call_401", "call_session_error") and method == "tools/call":
                    status = 401 if fixture.mode == "call_401" else 404
                elif fixture.mode == "redirect" and method == "tools/call":
                    status = 307
                elif fixture.mode == "discover_reconnect" and method == "tools/list":
                    fixture.mode, status = "normal", 404
                elif fixture.mode == "ordinary_reconnect" and method == "tools/call":
                    fixture.mode, status = "normal", 404
                elif fixture.mode == "initialize_retry" and method == "initialize":
                    fixture.mode, status = "normal", 404
                elif method == "initialize":
                    result = {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}},
                              "serverInfo": {"name": "minis-automation", "version": "1"}}
                elif method == "tools/list":
                    result = {"tools": [{"name": name, "inputSchema": {"type": "object"}}
                                        for name in sorted(transport.AUTOMATION_TOOLS)]}
                elif method == "tools/call":
                    if fixture.mode == "rpc_error":
                        error = {"code": -32000, "message": "session expired " + TOKEN}
                    elif fixture.mode == "tool_error_echo":
                        result = {"isError": True, "content": [{"type": "text", "text": "Authorization: Bearer " + TOKEN}],
                                  "structuredContent": {"headers": {"Authorization": "Bearer " + TOKEN}}}
                    else:
                        result = {"isError": False, "content": [{"type": "text", "text": "done"}]}
                response = {"jsonrpc": "2.0", "id": body.get("id")}
                response["error" if error else "result"] = error or result
                raw = ("session expired; Authorization: Bearer " + TOKEN).encode() if status != 200 else json.dumps(response).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Mcp-Session-Id", "mcp-" + fixture.cfg["session"])
                if status == 307:
                    self.send_header("Location", "http://127.0.0.1:%d/leak" % fixture.server.server_port)
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.addCleanup(self.server.server_close)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.server.shutdown)
        self.url_patch = patch.object(config, "AUTOMATION_URL", "http://127.0.0.1:%d/mcp" % self.server.server_port)
        self.url_patch.start()
        self.addCleanup(self.url_patch.stop)
        self.cfg = native_config()
        self.native_patch = patch.object(config, "local_automation_config", side_effect=lambda: copy.deepcopy(self.cfg))
        self.native_patch.start()
        self.addCleanup(self.native_patch.stop)

    def methods(self):
        return [event[0] for event in self.events]

    def transport(self):
        return transport.HTTPTransport(self.cfg, "minis-automation")

    def test_real_handshake_discovery_and_two_calls_reuse_one_session(self):
        client = self.transport()
        with patch.dict(os.environ, {"HTTP_PROXY": "http://127.0.0.1:1", "NO_PROXY": ""}):
            self.assertEqual(len(client.list_tools()), 22)
            self.assertFalse(client.call_tool("tap_screen", {"x": 1, "y": 2})["isError"])
            self.assertFalse(client.call_tool("input_text", {"text": "public marker"})["isError"])
        self.assertEqual(self.methods(), ["initialize", "notifications/initialized", "tools/list", "tools/call", "tools/call"])
        self.assertTrue(all(event[1] == "Bearer " + TOKEN for event in self.events))

    def test_ordinary_http_session_recovery_remains_available(self):
        client = transport.HTTPTransport({"url": config.AUTOMATION_URL, "headers": self.cfg["headers"]}, "ordinary")
        client.list_tools()
        self.events.clear()
        self.mode = "ordinary_reconnect"
        self.assertFalse(client.call_tool("ordinary_tool", {})["isError"])
        self.assertEqual(self.methods(), ["tools/call", "initialize", "notifications/initialized", "tools/call"])
        # Existing ordinary configs are allowed to omit headers or use null.
        self.assertEqual(daemon.MCPHTTPSession("ordinary", {"url": config.AUTOMATION_URL, "headers": None}).cfg["headers"], {})

    def test_discovery_can_reconnect_after_forgotten_session(self):
        client = self.transport()
        client.list_tools()
        self.events.clear()
        self.mode = "discover_reconnect"
        self.assertEqual(len(client.list_tools()), 22)
        self.assertEqual(self.methods(), ["tools/list", "initialize", "notifications/initialized", "tools/list"])

    def test_handshake_can_retry_before_one_tool_call_is_sent(self):
        self.mode = "initialize_retry"
        self.assertFalse(self.transport().call_tool("press_home", {})["isError"])
        self.assertEqual(self.methods(), ["initialize", "initialize", "notifications/initialized", "tools/call"])

    def test_sent_timeout_returns_unknown_without_replay_or_header_details(self):
        client = self.transport()
        client.list_tools()
        with patch.object(transport.httpx, "post", side_effect=transport.httpx.ReadTimeout("sensitive " + TOKEN)) as post, \
                self.assertRaises(transport.MCPError) as failure:
            client.call_tool("input_text", {"text": "one input"})
        self.assertEqual(post.call_count, 1)
        self.assertEqual(failure.exception.code, "AUTOMATION_OUTCOME_UNKNOWN")
        self.assertNotIn(TOKEN, str(failure.exception))

    def test_sent_tools_calls_never_replay_for_auth_session_rpc_disconnect_or_redirect(self):
        for mode in ("call_401", "call_session_error", "rpc_error", "call_disconnect", "redirect"):
            with self.subTest(mode=mode):
                client = self.transport()
                client.list_tools()
                self.events.clear()
                self.mode = mode
                with self.assertRaises(transport.MCPError) as failure:
                    client.call_tool("input_text", {"text": "send once"})
                self.assertEqual(failure.exception.code, "AUTOMATION_OUTCOME_UNKNOWN")
                self.assertNotIn(TOKEN, str(failure.exception))
                self.assertEqual(self.methods(), ["tools/call"])
                self.mode = "normal"

    def test_expiry_and_rotation_before_send_never_borrow_new_authorization(self):
        client = self.transport()
        client.list_tools()
        self.events.clear()
        self.cfg = None
        with self.assertRaises(transport.MCPError) as failure:
            client.call_tool("press_home", {})
        self.assertEqual(failure.exception.code, "AUTOMATION_UNAVAILABLE")
        self.assertEqual(self.events, [])
        self.cfg = native_config(OTHER_TOKEN, OTHER_SESSION)
        with self.assertRaises(transport.MCPError) as failure:
            client.call_tool("press_home", {})
        self.assertEqual(failure.exception.code, "AUTOMATION_SESSION_CHANGED")
        self.assertEqual(self.events, [])

    def test_pool_replaces_old_session_and_refuses_a_queued_old_call(self):
        server = daemon.DaemonServer("unused.port", "unused.pid")
        self.addCleanup(server.pool.shutdown)
        request = {"cmd": "call", "server": "MINIS-AUTOMATION", "tool": "tap_screen", "args": {"x": 4, "y": 6},
                   "automationSession": SESSION}
        self.assertTrue(server.handle_request(request)["ok"])
        old = server.pool.get("minis-automation")
        self.cfg = native_config(OTHER_TOKEN, OTHER_SESSION)
        delayed = server.handle_request(request)
        self.assertFalse(delayed["ok"])
        self.assertEqual(delayed["code"], "AUTOMATION_SESSION_CHANGED")
        self.assertEqual(self.methods().count("tools/call"), 1)
        request["automationSession"] = OTHER_SESSION
        self.assertTrue(server.handle_request(request)["ok"])
        self.assertIsNot(old, server.pool.get("minis-automation"))
        self.assertFalse(old.is_alive())
        with self.assertRaises(transport.MCPError):
            old.call_tool("input_text", {"text": "queued old input"})
        calls = [event for event in self.events if event[0] == "tools/call"]
        self.assertEqual([event[1] for event in calls], ["Bearer " + TOKEN, "Bearer " + OTHER_TOKEN])
        self.cfg = None
        response = server.handle_request(request)
        self.assertFalse(response["ok"])
        self.assertNotIn("minis-automation", server.pool.alive_servers())
        self.assertNotIn(TOKEN, json.dumps(response))
        self.assertNotIn(OTHER_TOKEN, json.dumps(response))

    def test_pool_and_cli_do_not_replay_after_sent_request_or_ipc_loss(self):
        server = daemon.DaemonServer("unused.port", "unused.pid")
        self.addCleanup(server.pool.shutdown)
        server.handle_request({"cmd": "tools", "server": "minis-automation", "automationSession": SESSION})
        self.events.clear()
        self.mode = "call_disconnect"
        response = server.handle_request({"cmd": "call", "server": "minis-automation", "tool": "press_home", "automationSession": SESSION})
        self.assertFalse(response["ok"])
        self.assertEqual(response["code"], "AUTOMATION_OUTCOME_UNKNOWN")
        self.assertEqual(self.methods(), ["tools/call"])
        output = io.StringIO()
        with patch.object(cli, "maybe_start_daemon"), patch.object(cli, "send_to_daemon", return_value={
                "ok": False, "code": "NO_DAEMON", "error": "closed"}) as send, \
                patch.object(cli, "_log"), contextlib.redirect_stdout(output), self.assertRaises(SystemExit):
            cli.call_daemon({"cmd": "call", "server": "MINIS-AUTOMATION", "tool": "input_text"}, False)
        self.assertEqual(send.call_count, 1)
        self.assertEqual(send.call_args.args[0]["automationSession"], SESSION)
        self.assertNotIn(TOKEN, json.dumps(send.call_args.args[0]))
        self.assertEqual(json.loads(output.getvalue())["code"], "AUTOMATION_OUTCOME_UNKNOWN")

    def test_unsupported_call_never_reaches_http(self):
        with self.assertRaises(transport.MCPError) as failure:
            self.transport().call_tool("run_command", {"command": "not permitted"})
        self.assertEqual(failure.exception.code, "TOOL_NOT_FOUND")
        self.assertEqual(self.events, [])

    def test_http_200_tool_error_cannot_echo_native_credentials(self):
        self.mode = "tool_error_echo"
        result = self.transport().call_tool("tap_screen", {"x": 1, "y": 1})
        self.assertTrue(result["isError"])
        self.assertNotIn(TOKEN, json.dumps(result))
        self.assertEqual(self.methods().count("tools/call"), 1)

    def test_cli_info_and_list_hide_auth_and_reserved_add_cannot_write(self):
        output = io.StringIO()
        with patch.object(cli, "_require_server", return_value=self.cfg), contextlib.redirect_stdout(output):
            cli.cmd_info(["MINIS-AUTOMATION"], False)
        self.assertNotIn(TOKEN, output.getvalue())
        server = daemon.DaemonServer("unused.port", "unused.pid")
        with patch.object(config, "get_servers", return_value={"minis-automation": self.cfg}):
            response = server.handle_request({"cmd": "list", "all": True})
        self.assertNotIn(TOKEN, json.dumps(response))
        with patch.object(config, "upsert_server") as store, patch.object(cli, "_log"), \
                contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit):
            cli.cmd_add(["--name", "MINIS-AUTOMATION", "--url", "https://example.invalid"], False)
        store.assert_not_called()


class ImageValidationTests(unittest.TestCase):
    def test_cli_materializes_only_automation_screenshot_and_retains_other_servers(self):
        source = screenshot()
        expected_capture = {"path": "/cwd/.minis-automation-captures/fixture.png", "sha256": hashlib.sha256(png()).hexdigest(),
                            "mimeType": "image/png", "width": 2, "height": 3, "bytes": len(png())}
        output = io.StringIO()
        with patch.object(cli, "call_daemon", return_value=source), \
                patch.object(images, "save_capture", return_value=expected_capture) as save, contextlib.redirect_stdout(output):
            cli.cmd_call(["MINIS-AUTOMATION", "screenshot"], False)
        saved = json.loads(output.getvalue())
        self.assertEqual(saved["captures"], [expected_capture])
        self.assertIn("read_image", output.getvalue())
        self.assertNotIn(source["result"]["content"][0]["data"], output.getvalue())
        save.assert_called_once()
        output = io.StringIO()
        with patch.object(cli, "call_daemon", return_value=source), \
                patch.object(images, "save_capture") as save, contextlib.redirect_stdout(output):
            cli.cmd_call(["ordinary", "screenshot"], False)
        self.assertEqual(json.loads(output.getvalue()), source)
        save.assert_not_called()

    def test_png_header_dimensions_and_checksum(self):
        data, mime, width, height = images.decode_image(screenshot()["result"]["content"][0])
        self.assertEqual((data, mime, width, height), (png(), "image/png", 2, 3))
        altered = bytearray(png())
        altered[29] ^= 1
        with self.assertRaises(images.AutomationImageError):
            images.decode_image(screenshot(bytes(altered))["result"]["content"][0])

    def test_jpeg_marker_dimensions_and_truncation(self):
        # A small structural fixture exercises SOF/SOS/stuffed entropy markers;
        # this is image-header validation, not a pixel decoder acceptance test.
        jpeg = (b"\xff\xd8\xff\xc0" + struct.pack(">H B H H B", 11, 8, 2, 3, 1)
                + b"\x01\x11\x00\xff\xda\x00\x08\x01\x01\x00\x00\x3f\x00\x11\xff\x00\x12\xff\xd9")
        self.assertEqual(images.decode_image(screenshot(jpeg, "image/jpeg")["result"]["content"][0])[2:], (3, 2))
        for value in (jpeg[:-2], jpeg + b"trailing", jpeg.replace(b"\xff\xc0", b"\xff\xe0", 1)):
            with self.subTest(length=len(value)), self.assertRaises(images.AutomationImageError):
                images.decode_image(screenshot(value, "image/jpeg")["result"]["content"][0])

    def test_mime_base64_size_and_dimension_rejections(self):
        original = screenshot()["result"]["content"][0]
        bad = [dict(original, mimeType="image/gif"), dict(original, mimeType="image/jpeg"),
               dict(original, data=original["data"] + "\n"), dict(original, data="not base64!"),
               dict(original, data=""), dict(original, data="é"),
               dict(original, data=base64.b64encode(b"not an image").decode())]
        for block in bad:
            with self.subTest(mime=block["mimeType"]), self.assertRaises(images.AutomationImageError):
                images.decode_image(block)
        with patch.object(images, "MAX_IMAGE_BYTES", 4), self.assertRaises(images.AutomationImageError):
            images.decode_image(original)
        for width, height in ((0, 1), (1, 8193), (8192, 8192)):
            with self.subTest(width=width, height=height), self.assertRaises(images.AutomationImageError):
                images.dimensions(width, height)

    @unittest.skipUnless(CAN_STORE, "secure directory descriptors unavailable; exercised by fail-closed test")
    def test_capture_is_uuid_0600_in_cwd_and_output_has_no_base64(self):
        with tempfile.TemporaryDirectory() as temp, current_directory(temp):
            response = images.materialize_screenshot(screenshot())
            capture = response["captures"][0]
            target = Path(capture["path"])
            self.assertEqual(target.parent, Path(os.getcwd()) / images.CAPTURE_DIR)
            self.assertEqual(str(uuid.UUID(target.stem)), target.stem)
            self.assertEqual(target.read_bytes(), png())
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)
            self.assertEqual(capture["sha256"], hashlib.sha256(png()).hexdigest())
            serialized = json.dumps(response, ensure_ascii=False)
            self.assertNotIn(screenshot()["result"]["content"][0]["data"], serialized)
            self.assertIn("read_image", serialized)
            self.assertNotEqual(target, Path("/etc/not-selected.png"))

    @unittest.skipUnless(CAN_STORE, "POSIX symlink fixture")
    def test_symlink_capture_parent_and_uuid_collision_fail_closed(self):
        with tempfile.TemporaryDirectory() as temp, current_directory(temp):
            real = Path(temp) / "elsewhere"
            real.mkdir()
            Path(images.CAPTURE_DIR).symlink_to(real, target_is_directory=True)
            with self.assertRaises(images.AutomationImageError):
                images.materialize_screenshot(screenshot())
            self.assertEqual(list(real.iterdir()), [])
            Path(images.CAPTURE_DIR).unlink()
            capture_dir = Path(images.CAPTURE_DIR)
            capture_dir.mkdir(mode=0o700)
            existing = capture_dir / (SESSION + ".png")
            existing.write_bytes(b"keep")
            with patch.object(images.uuid, "uuid4", return_value=uuid.UUID(SESSION)), \
                    self.assertRaises(images.AutomationImageError):
                images.materialize_screenshot(screenshot())
            self.assertEqual(existing.read_bytes(), b"keep")

    @unittest.skipUnless(CAN_STORE, "POSIX symlink fixture")
    def test_symlink_above_capture_directory_and_unsafe_permissions_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            real = Path(temp) / "real"
            real.mkdir()
            alias = Path(temp) / "alias"
            alias.symlink_to(real, target_is_directory=True)
            with patch.object(images.os, "getcwd", return_value=str(alias)), self.assertRaises(images.AutomationImageError):
                images.materialize_screenshot(screenshot())
            with current_directory(real):
                capture_dir = Path(images.CAPTURE_DIR)
                capture_dir.mkdir(mode=0o700)
                capture_dir.chmod(0o777)
                with self.assertRaises(images.AutomationImageError):
                    images.materialize_screenshot(screenshot())

    @unittest.skipIf(CAN_STORE, "POSIX supports secure capture storage")
    def test_unsupported_filesystem_protection_is_not_silently_weakened(self):
        with tempfile.TemporaryDirectory() as temp, current_directory(temp), self.assertRaises(images.AutomationImageError):
            images.materialize_screenshot(screenshot())

    def test_failed_or_path_only_result_does_not_read_server_selected_path(self):
        failure = {"result": {"isError": True, "content": [{"type": "text", "text": "unavailable"}]}}
        with patch.object(images, "save_capture") as save:
            self.assertIs(images.materialize_screenshot(failure), failure)
            with self.assertRaises(images.AutomationImageError):
                images.materialize_screenshot({"result": {"content": [{"type": "resource", "path": "/etc/secret"}]}})
            save.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
