"""Verify the local MCP configuration and, on Actions, the production Swift approval gate."""
import argparse
import importlib.util
import json
import io
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
IOS = ROOT / "src/ios"
APPROVAL = IOS / "Diagnostics/DeviceMCPApproval.swift"
BRIDGE = IOS / "Diagnostics/DeviceMCPNativeBridge.swift"
CONFIG = IOS / "default_mount/usr/local/lib/minis-mcp-cli/utils/config.py"
SPEC = importlib.util.spec_from_file_location("device_test_config", CONFIG)
config = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(config)
SERVER = IOS / "default_mount/usr/local/lib/minis-mcp-cli/device_server.py"
SERVER_SPEC = importlib.util.spec_from_file_location("device_mcp_server_test", SERVER)
device_server = importlib.util.module_from_spec(SERVER_SPEC)
SERVER_SPEC.loader.exec_module(device_server)


class DeviceConfigTests(unittest.TestCase):
    def test_ordinary_configs_unchanged_without_native_component(self):
        user_server = {"url": "https://example.invalid/ordinary"}
        with patch.object(config, "load_config", return_value={"mcpServers": {"minis-device": user_server}}), \
                patch.object(config, "_local_device_state", return_value=None):
            self.assertIsNone(config.get_server("minis-device"))

    def test_config_and_sync_file_cannot_choose_local_transport(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "servers.json"
            with patch.object(config, "CONFIG_DIR", temp), patch.object(config, "CONFIG_PATH", str(path)):
                config.upsert_server("minis-device", {"url": "https://example.invalid/substitute"})
                config.upsert_server("ordinary", {"url": "https://example.invalid/ordinary"})
                original = path.read_bytes()
                with patch.object(config, "_local_device_state", return_value=False):
                    self.assertEqual(list(config.get_servers()), ["ordinary"])
                with patch.object(config, "_local_device_state", return_value=True):
                    server = config.get_server("minis-device")
                    self.assertEqual(server["command"], "/usr/bin/python3")
                    self.assertEqual(server["args"], ["-I", "-S", "-B", "/usr/local/lib/minis-mcp-cli/device_server.py"])
                    self.assertFalse({"url", "headers", "env", "oauth"} & set(server))
                self.assertEqual(original, path.read_bytes())

    def test_reserved_name_cannot_be_written_or_toggled(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(config, "CONFIG_DIR", temp), \
                patch.object(config, "CONFIG_PATH", str(Path(temp) / "servers.json")):
            self.assertFalse(config.upsert_server("minis-device", {"command": "/bin/evil"}))
            self.assertFalse(config.remove_server("minis-device"))
            self.assertFalse(config.set_enabled("minis-device", True))


class DeviceProtocolTests(unittest.TestCase):
    def test_handshake_tools_and_call_shape(self):
        session = device_server.Session()
        result = session.dispatch({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                   "params": {"protocolVersion": "2025-06-18"}})
        self.assertEqual(result["serverInfo"]["name"], "minis-device")
        self.assertIsNone(session.dispatch({"jsonrpc": "2.0", "method": "notifications/initialized"}))
        listing = session.dispatch({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        self.assertEqual({tool["name"] for tool in listing["tools"]}, set(device_server.TOOLS))
        with patch.object(device_server, "call_native", return_value={
                "isError": False,
                "structuredContent": {"request_id": "00000000-0000-4000-8000-000000000001",
                                      "operation": "get_brightness", "ok": True, "data": {"brightness": 0.5}},
                "content": [{"type": "text", "text": "ok"}]}) as native:
            reply = session.dispatch({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                                      "params": {"name": "get_brightness", "arguments": {}}})
        self.assertFalse(reply["isError"])
        self.assertEqual(reply["structuredContent"]["operation"], "get_brightness")
        native.assert_called_once_with("get_brightness", [])

    def test_invalid_and_forbidden_arguments_never_call_native(self):
        session = device_server.Session()
        session.dispatch({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                          "params": {"protocolVersion": "2025-06-18"}})
        session.dispatch({"jsonrpc": "2.0", "method": "notifications/initialized"})
        with patch.object(device_server, "call_native") as native:
            with self.assertRaises(device_server.RPCError):
                session.dispatch({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                                  "params": {"name": "set_brightness", "arguments": {"percent": 1.5}}})
            with self.assertRaises(device_server.RPCError):
                session.dispatch({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                                  "params": {"name": "get_brightness", "arguments": {"percent": 1}}})
            native.assert_not_called()

    def test_decode_rejects_duplicate_and_nonfinite_json(self):
        with self.assertRaises(ValueError):
            device_server.decode('{"jsonrpc":"2.0","jsonrpc":"2.0"}')
        with self.assertRaises(ValueError):
            device_server.decode('{"value":NaN}')

    def test_serve_emits_json_rpc_ids_and_notifications_have_no_reply(self):
        input_stream = io.BytesIO(b'{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18"}}\n'
                                  b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n'
                                  b'{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}\n')
        output_stream = io.BytesIO()
        device_server.serve(input_stream, output_stream)
        replies = [json.loads(line) for line in output_stream.getvalue().splitlines()]
        self.assertEqual([reply["id"] for reply in replies], [1, 2])
        self.assertNotIn(b"notifications/initialized", output_stream.getvalue())

    def test_availability_errors_hide_local_server(self):
        with patch.object(config.os.path, "isfile", return_value=True):
            for output, code, expected in [(b'{"enabled":true}', 0, True), (b'{"enabled":false}', 0, False),
                                           (b'garbage', 0, False), (b'{"enabled":true}', 4, False)]:
                with self.subTest(output=output, code=code), patch.object(subprocess, "run", return_value=
                        subprocess.CompletedProcess([], code, output, b'')):
                    self.assertIs(config._local_device_state(), expected)
            with patch.object(subprocess, "run", side_effect=subprocess.TimeoutExpired("bridge", 2)):
                self.assertFalse(config._local_device_state())

    def test_only_main_app_owns_native_execution(self):
        project = (IOS / "Minis.xcodeproj/project.pbxproj").read_text(encoding="utf-8")
        main = project.split("E51000041 /* Sources */ = {", 1)[1].split("runOnlyForDeploymentPostprocessing", 1)[0]
        for name in ("DeviceMCPNativeBridge.swift", "DeviceMCPApproval.swift", "DeviceMCPOffload.m"):
            self.assertIn(name + " in Sources", main)
            self.assertEqual(project.count(name + " in Sources"), 2)
        offload = (IOS / "NativeOffloads/DeviceMCPOffload.m").read_text(encoding="utf-8")
        self.assertIn('native_offload_add_handler("minis-device-call", device_mcp_call)', offload)
        self.assertIn('"com.openminis.app.root"', offload)
        self.assertTrue(offload.startswith("#if defined(MINIS_ROOT_MANAGEMENT) && MINIS_ROOT_MANAGEMENT"))
        self.assertIn("#if MINIS_ROOT_MANAGEMENT && os(iOS)", BRIDGE.read_text(encoding="utf-8"))

    def test_reserved_server_cannot_enter_the_native_store_or_sync(self):
        store = (IOS / "Agent/Session/MCPStore.swift").read_text(encoding="utf-8")
        self.assertIn("guard !Self.isReservedServerName(name)", store)
        self.assertIn("result.removeAll { Self.isReservedServerName($0.id) }", store)
        self.assertIn("guard !Self.isReservedServerName(name) else { return nil }", store)
        self.assertIn("guard !Self.isReservedServerName(server.id) else", store)

    def test_device_mcp_ui_has_simplified_and_traditional_chinese(self):
        catalog = json.loads((IOS / "Localizable.xcstrings").read_text(encoding="utf-8"))["strings"]
        keys = {
            "Allow device tool requests", "Show device tools", "Copy device activity report",
            "Clear device activity report", "This device", "Minis device tools",
            "Deny", "Allow once", "Allow once: %@?",
            "Allows model, screen size and display brightness requests while Minis is in the foreground. Each call asks you to allow it once. Leaving the app turns this off.",
            "Share this device’s model and iOS version with the requesting tool?",
            "Share the screen dimensions and scale with the requesting tool?",
            "Share the current display brightness with the requesting tool?",
            "Set display brightness?",
            "The matching bundled helper reported UID 0 and EUID 0 for this operation.",
            "Root execution has not been verified by this result.",
        }
        keys.update(device_server.TOOLS.values())
        # Root operation titles and protocol failures remain stable English
        # values for reports; their presentation resolves through the catalog.
        view = (IOS / "Views/Settings/RootManagementView.swift").read_text(encoding="utf-8")
        keys.update(re.findall(r'(?:Text|Section|row|Toggle|Button|navigationTitle)\("([^"\\]*)"', view))
        for path in ("Diagnostics/RootManagementAuthorization.swift", "Diagnostics/RootManagementRunner.swift"):
            source = (IOS / path).read_text(encoding="utf-8")
            keys.update(re.findall(r'(?:return |validationError = )"([^"\\]+[ .][^"\\]*)"', source))
            keys.update(re.findall(r'return \(nil, "([^"\\]+)"\)', source))
        keys.discard("iOS")  # Product name, identical in every language.
        for key in sorted(keys):
            with self.subTest(key=key):
                localizations = catalog[key]["localizations"]
                for locale in ("en", "zh-Hans", "zh-Hant"):
                    unit = localizations[locale]["stringUnit"]
                    self.assertEqual(unit["state"], "translated")
                    self.assertTrue(unit["value"].strip())
                    self.assertEqual(unit["value"].count("%@"), key.count("%@"))
                    if locale != "en":
                        self.assertNotEqual(unit["value"], key)


def native():
    with tempfile.TemporaryDirectory(prefix="minis-device-mcp-") as temp:
        binary = Path(temp) / "approval"
        subprocess.run(["xcrun", "swiftc", "-swift-version", "5", "-warnings-as-errors", "-parse-as-library",
                        "-D", "MINIS_ROOT_MANAGEMENT", str(APPROVAL), str(ROOT / "tests/DeviceMCPApprovalTests.swift"),
                        "-o", str(binary)], check=True, timeout=90)
        subprocess.run([str(binary)], check=True, timeout=10)
        sdk = subprocess.check_output(["xcrun", "--sdk", "iphoneos", "--show-sdk-path"], text=True).strip()
        runner = IOS / "Diagnostics/RootManagementRunner.swift"
        headers = Path(temp) / "DeviceMCP.h"
        headers.write_text('#include "RootManagementClient.h"\n#include "DeviceMCPOffload.h"\n', encoding="utf-8")
        # The bundle override lives in MinisApp.swift. Supply only that seam
        # while type-checking the production localization helper and bridge;
        # the full Actions app build checks the actual override integration.
        localization_bundle = Path(temp) / "LocalizationBundle.swift"
        localization_bundle.write_text(
            'import Foundation\nextension Bundle { var languageBundle: Bundle? { nil } }\n',
            encoding="utf-8")
        subprocess.run(["xcrun", "--sdk", "iphoneos", "swiftc", "-typecheck", "-parse-as-library", "-swift-version", "5",
                        "-warnings-as-errors", "-target", "arm64-apple-ios15.0", "-sdk", sdk,
                        "-D", "MINIS_ROOT_MANAGEMENT", "-Xcc", "-DMINIS_ROOT_MANAGEMENT=1", "-Xcc", "-I" + str(runner.parent),
                        "-Xcc", "-I" + str(IOS / "NativeOffloads"), "-import-objc-header", str(headers),
                        str(runner.with_name("RootManagementAuthorization.swift")),
                        str(runner), str(APPROVAL), str(IOS / "Shared/AppLocalization.swift"),
                        str(localization_bundle), str(BRIDGE),
                        str(IOS / "Views/Settings/RootManagementView.swift")], check=True, timeout=180)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--native", action="store_true")
    args = parser.parse_args()
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__]))
    if not result.wasSuccessful():
        raise SystemExit(1)
    if args.native:
        native()
