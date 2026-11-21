"""Local STDIO MCP adapter for the four device actions confirmed on the iPhone."""

import json
import math
import subprocess
import sys
import uuid


OFFLOAD = "/usr/local/bin/minis-device-call"
PROTOCOL = "2025-06-18"
MAX_LINE = 8192
TOOLS = {
    "get_device_info": "Read the model and system version after confirmation on the device.",
    "get_screen_info": "Read the screen size in points and its scale after confirmation on the device.",
    "get_brightness": "Read display brightness (0 to 1) after confirmation on the device.",
    "set_brightness": "Set display brightness to an integer percentage after confirmation on the device.",
}


class RPCError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def decode(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate key")
            result[key] = value
        return result

    def reject(value):
        raise ValueError("non-finite JSON number")

    return json.loads(raw, object_pairs_hook=unique, parse_constant=reject)


def tool_definitions():
    for name, description in TOOLS.items():
        schema = {"type": "object", "properties": {}, "additionalProperties": False}
        if name == "set_brightness":
            schema.update(properties={"percent": {"type": "integer", "minimum": 0, "maximum": 100}}, required=["percent"])
        yield {"name": name, "description": description, "inputSchema": schema}


def call_arguments(params):
    name = params.get("name")
    arguments = params.get("arguments", {})
    if not isinstance(name, str) or name not in TOOLS or not isinstance(arguments, dict):
        raise RPCError(-32602, "Unknown device tool or invalid arguments.")
    if name == "set_brightness":
        percent = arguments.get("percent")
        if set(arguments) != {"percent"} or type(percent) is not int or not 0 <= percent <= 100:
            raise RPCError(-32602, "percent must be an integer from 0 through 100.")
        return name, [str(percent)]
    if arguments:
        raise RPCError(-32602, "This device tool takes no arguments.")
    return name, []


def finite(value, low, high):
    return type(value) in (float, int) and low <= value <= high and math.isfinite(value)


def valid_data(tool, data):
    if not isinstance(data, dict):
        return False
    if tool == "get_device_info":
        return (set(data) == {"model", "systemName", "systemVersion"}
                and all(isinstance(value, str) and 0 < len(value) <= 128 for value in data.values()))
    if tool == "get_screen_info":
        return (set(data) == {"widthPoints", "heightPoints", "scale"}
                and all(finite(data[key], 1, 32768) for key in ("widthPoints", "heightPoints"))
                and finite(data["scale"], 1, 8))
    return set(data) == {"brightness"} and finite(data["brightness"], 0, 1)


def call_native(tool, args):
    request_id = str(uuid.uuid4())
    identity = {"request_id": request_id, "operation": tool}
    try:
        process = subprocess.run([OFFLOAD, request_id, tool, *args], stdin=subprocess.DEVNULL,
                                 stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=45, check=False)
        if len(process.stdout) > 4096:
            raise ValueError("oversized response")
        reply = decode(process.stdout.decode("utf-8"))
        if not isinstance(reply, dict) or not all(reply.get(key) == value for key, value in identity.items()):
            raise ValueError("response mismatch")
        if reply.get("ok") is True:
            if set(reply) != {*identity, "ok", "data"} or process.returncode != 0 or not valid_data(tool, reply["data"]):
                raise ValueError("invalid result")
        elif (reply.get("ok") is not False or set(reply) != {*identity, "ok", "error"} or process.returncode == 0
              or reply["error"] not in ("invalid", "duplicate", "limit", "busy", "unavailable", "revoked", "denied", "expired")):
            raise ValueError("invalid error")
    except subprocess.TimeoutExpired:
        reply = dict(identity, ok=False, error="expired")
    except (ValueError, OSError, RecursionError):
        reply = dict(identity, ok=False, error="unavailable")
    return {"isError": not reply["ok"], "structuredContent": reply,
            "content": [{"type": "text", "text": json.dumps(reply, separators=(",", ":"), allow_nan=False)}]}


class Session:
    def __init__(self):
        self.handshake = False
        self.ready = False

    def dispatch(self, message):
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0" or not isinstance(message.get("method"), str):
            raise RPCError(-32600, "Expected a JSON-RPC 2.0 request.")
        method = message["method"]
        params = message.get("params", {})
        if "id" not in message:
            if method == "notifications/initialized" and self.handshake:
                self.ready = True
            return None
        if type(message["id"]) not in (int, str) or not isinstance(params, dict):
            raise RPCError(-32600, "Invalid request ID or parameters.")
        if method == "initialize" and not self.handshake:
            if not isinstance(params.get("protocolVersion"), str):
                raise RPCError(-32602, "Missing protocol version.")
            self.handshake = True
            return {"protocolVersion": PROTOCOL, "capabilities": {"tools": {}},
                    "serverInfo": {"name": "minis-device", "version": "1"}}
        if not self.ready:
            raise RPCError(-32600, "Initialize the device MCP session first.")
        if method == "ping":
            return {}
        if method == "tools/list":
            return {"tools": list(tool_definitions())}
        if method == "tools/call":
            return call_native(*call_arguments(params))
        raise RPCError(-32601, "Method not supported by minis-device.")


def serve(input_stream, output_stream):
    session = Session()
    while True:
        line = input_stream.readline(MAX_LINE + 1)
        if not line:
            return
        request_id = None
        try:
            if len(line) > MAX_LINE:
                while line and not line.endswith(b"\n"):
                    line = input_stream.readline(MAX_LINE + 1)
                raise RPCError(-32600, "Request too large.")
            message = decode(line.decode("utf-8"))
            if isinstance(message, dict) and type(message.get("id")) in (int, str):
                request_id = message["id"]
            result = session.dispatch(message)
            if result is None:
                continue
            payload = {"result": result}
        except RPCError as error:
            payload = {"error": {"code": error.code, "message": str(error)}}
        except (ValueError, RecursionError):
            payload = {"error": {"code": -32700, "message": "Invalid JSON."}}
        output_stream.write((json.dumps(dict(payload, jsonrpc="2.0", id=request_id)) + "\n").encode("utf-8"))
        output_stream.flush()


if __name__ == "__main__":
    serve(sys.stdin.buffer, sys.stdout.buffer)
