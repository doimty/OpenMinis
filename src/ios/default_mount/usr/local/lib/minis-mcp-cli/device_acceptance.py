#!/usr/bin/env python3
"""Collect physical-device MCP receipts. Every device operation retains its normal confirmation."""

import argparse
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import uuid


CLI = "/usr/local/bin/minis-mcp-cli"
SERVER = "minis-device"
TOOLS = {"get_device_info", "get_screen_info", "get_brightness", "set_brightness"}
TOLERANCE = 0.02


class CaptureError(Exception):
    pass


def require(condition, message):
    if not condition:
        raise CaptureError(message)


def speak(message):
    print(message, file=sys.stderr, flush=True)


def run_command(command):
    return subprocess.run(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, timeout=120, check=False)


def new_output(path):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    return os.fdopen(descriptor, "wb")


def native_brightness(observation):
    data = observation.get("data")
    value = data.get("brightness") if isinstance(data, dict) else None
    require(type(value) in (int, float) and 0 <= value <= 1 and math.isfinite(value), "未收到有效的亮度读数。")
    return value


def percent_argument(discovery):
    tools = discovery.get("tools")
    require(discovery.get("server") == SERVER and type(discovery.get("count")) is int and discovery["count"] == 4
            and type(tools) is list and len(tools) == 4 and all(type(entry) is dict for entry in tools)
            and {entry.get("name") for entry in tools} == TOOLS, "本机构建没有公布预期的四个设备工具。")
    schema = next(entry for entry in tools if entry["name"] == "set_brightness").get("inputSchema")
    require(isinstance(schema, dict) and schema.get("type") == "object", "缺少亮度工具参数说明。")
    fields = schema.get("properties")
    required = schema.get("required")
    require(isinstance(fields, dict) and isinstance(required, list) and len(required) == 1
            and type(required[0]) is str and set(fields) == {required[0]}, "亮度工具的输入字段不符。")
    field = fields[required[0]]
    require(isinstance(field, dict) and field.get("type") == "integer"
            and type(field.get("minimum")) is int and field["minimum"] == 0
            and type(field.get("maximum")) is int and field["maximum"] == 100,
            "亮度工具未声明 0–100 的整数百分比。")
    return required[0]


def capture(output, *, runner=run_command, inform=speak):
    output = output.absolute()
    original_percent = None
    restoration_needed = False
    seen = set()

    def record_command(stream, arguments):
        process = runner([CLI, *arguments])
        raw = process.stdout
        if raw:
            stream.write(raw)
            if not raw.endswith(b"\n"):
                stream.write(b"\n")
            stream.flush()
        require(process.returncode == 0, f"CLI 返回退出码 {process.returncode}；原始输出保留在回执文件。")
        require(raw and len(raw.splitlines()) == 1, "CLI 未返回单行 JSON；保留已收到的内容。")
        try:
            response = json.loads(raw)
        except (ValueError, UnicodeError) as error:
            raise CaptureError("CLI 返回了无效 JSON。") from error
        require(isinstance(response, dict), "CLI 输出类型不符。")
        return response

    def call(stream, operation, decision, prompt, arguments=None):
        inform(prompt)
        command = ["call", SERVER, operation]
        if arguments is not None:
            command.append(json.dumps(arguments, separators=(",", ":")))
        response = record_command(stream, command)
        require(response.get("server") == SERVER and response.get("tool") == operation, "回执的服务器或工具名称不符。")
        result = response.get("result")
        require(isinstance(result, dict), "MCP 回执缺少 result。")
        payload = result.get("structuredContent")
        require(isinstance(payload, dict) and payload.get("operation") == operation, "原生操作与 MCP 回执不符。")
        try:
            request_id = uuid.UUID(payload.get("request_id", ""))
        except (ValueError, AttributeError, TypeError) as error:
            raise CaptureError("没有可关联的原生请求 UUID。") from error
        require(request_id.version == 4 and request_id not in seen, "请求 UUID 无效或重复。")
        seen.add(request_id)
        allowed = decision == "allowed"
        require(result.get("isError") is (not allowed) and payload.get("ok") is allowed,
                "收到的授权决定与此步要求不符。")
        if not allowed:
            require(payload.get("error") == "denied" and "data" not in payload, "此步必须由用户明确点拒绝。")
        return payload

    with new_output(output) as receipts:
        inform(f"正在保存原始 MCP JSONL：{output}")
        inform("请保持 Minis 在前台。在 This device 中启用设备工具并清空此前的活动报告。")
        inform("正常会出现九次原生确认；只拒绝第 4 次。回执包含设备型号、系统版本、屏幕尺寸、亮度及请求 UUID。")
        try:
            field = percent_argument(record_command(receipts, ["refresh", SERVER]))
            call(receipts, "get_device_info", "allowed", "1/9 读取设备信息：点 Allow once。")
            call(receipts, "get_screen_info", "allowed", "2/9 读取屏幕尺寸：点 Allow once。")
            baseline = native_brightness(call(receipts, "get_brightness", "allowed", "3/9 读取原亮度：点 Allow once。"))
            original_percent = int(math.floor(baseline * 100 + 0.5))
            target = 70 if baseline <= 0.5 else 30
            inform(f"原亮度 {baseline * 100:.2f}%（恢复值 {original_percent}%）；测试值 {target}%。")
            restoration_needed = True
            call(receipts, "set_brightness", "denied", f"4/9 请求改成 {target}%：这一次务必点 Deny。", {field: target})
            unchanged = native_brightness(call(receipts, "get_brightness", "allowed", "5/9 确认拒绝后亮度未变：点 Allow once。"))
            require(abs(unchanged - baseline) <= TOLERANCE, "拒绝后的读数与原亮度不符。")
            write = native_brightness(call(receipts, "set_brightness", "allowed", f"6/9 允许改成 {target}%：点 Allow once。", {field: target}))
            changed = native_brightness(call(receipts, "get_brightness", "allowed", "7/9 读回新的亮度：点 Allow once。"))
            require(max(abs(write - target / 100), abs(changed - target / 100)) <= TOLERANCE, "写入或独立读回未达到测试亮度。")
            write = native_brightness(call(receipts, "set_brightness", "allowed", f"8/9 恢复原亮度 {original_percent}%：点 Allow once。", {field: original_percent}))
            final = native_brightness(call(receipts, "get_brightness", "allowed", "9/9 独立读回恢复结果：点 Allow once。"))
            require(max(abs(write - baseline), abs(final - baseline)) <= TOLERANCE, "最终读回与原亮度不符。")
            restoration_needed = False
        finally:
            if restoration_needed:
                inform(f"采集中断，未确认亮度已恢复。请用控制中心手动恢复到 {original_percent}%。未重试操作，已采集回执保留在 {output}。")
    inform(f"原始回执文件：{output}。请在 Settings → MCP Integrations → This device 中复制设备活动报告，另存为 device.json。")
    inform("两份文件一起交给离线校验器，才可核对从 MCP 到原生操作的完整链路。")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="new receipt JSONL path in an existing writable directory; never overwritten")
    args = parser.parse_args(argv)
    try:
        capture(args.output.expanduser())
    except KeyboardInterrupt:
        speak("用户中止采集；已有回执已保留。")
        return 130
    except (OSError, subprocess.TimeoutExpired, CaptureError) as error:
        speak(f"采集未完成：{error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
