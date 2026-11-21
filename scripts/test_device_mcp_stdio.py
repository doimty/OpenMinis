#!/usr/bin/env python3
"""Protocol fixtures for the local minis-device MCP server."""
from __future__ import annotations

import importlib.util
import io
import json
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src/ios/default_mount/usr/local/lib/minis-mcp-cli/device_server.py"
SPEC = importlib.util.spec_from_file_location("device_server", SOURCE)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def rpc(method, ident=1, params=None):
    message = {"jsonrpc": "2.0", "id": ident, "method": method}
    if params is not None:
        message["params"] = params
    return json.dumps(message, separators=(",", ":")) + "\n"


class DeviceMCPProtocolTests(unittest.TestCase):
    def run_server(self, text):
        output = io.BytesIO()
        MODULE.serve(io.BytesIO(text.encode()), output)
        return [json.loads(line) for line in output.getvalue().decode().splitlines()]

    def test_initialize_discovery_and_notification(self):
        messages = self.run_server(
            rpc("initialize", params={"protocolVersion": "2025-06-18"})
            + json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n"
            + rpc("tools/list", 2)
        )
        self.assertEqual(messages[0]["result"]["serverInfo"]["name"], "minis-device")
        tools = messages[1]["result"]["tools"]
        self.assertEqual({tool["name"] for tool in tools}, MODULE.TOOLS.keys())
        set_tool = next(tool for tool in tools if tool["name"] == "set_brightness")
        self.assertEqual(set_tool["inputSchema"]["required"], ["percent"])

    def test_each_call_is_one_native_request_and_preserves_structured_result(self):
        native = {"ok": True, "request_id": "12345678-1234-4234-8234-123456789abc",
                  "operation": "get_brightness", "data": {"brightness": 0.5}}
        with patch.object(MODULE, "call_native", return_value={
            "isError": False, "structuredContent": native,
            "content": [{"type": "text", "text": json.dumps(native, separators=(",", ":"))}],
        }) as call:
            messages = self.run_server(
                rpc("initialize", params={"protocolVersion": "2025-06-18"})
                + json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n"
                + rpc("tools/call", 2, {"name": "get_brightness", "arguments": {}})
            )
        call.assert_called_once_with("get_brightness", [])
        result = messages[1]["result"]
        self.assertEqual(result["structuredContent"], native)
        self.assertFalse(result["isError"])

    def test_write_arguments_are_bounded_and_invalid_requests_never_call_native(self):
        with patch.object(MODULE, "call_native") as call:
            messages = self.run_server(
                rpc("initialize", params={"protocolVersion": "2025-06-18"})
                + json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n"
                + rpc("tools/call", 2, {"name": "set_brightness", "arguments": {"percent": True}})
            )
        call.assert_not_called()
        self.assertEqual(messages[1]["error"]["code"], -32602)

    def test_duplicate_json_fields_are_rejected(self):
        messages = self.run_server(
            '{"jsonrpc":"2.0","id":1,"method":"initialize","method":"tools/list"}\n'
        )
        self.assertEqual(messages[0]["error"]["code"], -32700)


if __name__ == "__main__":
    unittest.main(verbosity=2)
