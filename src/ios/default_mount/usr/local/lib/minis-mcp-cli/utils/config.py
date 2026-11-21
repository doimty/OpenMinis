"""servers.json read/write — mcpServers object format (Claude-Desktop compatible).

Shared by iOS (iSH) and Android (PRoot). The same file is also read/written by
the native Settings UI (Swift MCPStore / Kotlin), so the on-disk schema must
stay exactly the mcpServers object form:

    {
      "mcpServers": {
        "<name>": {
          "url": "...", "headers": {...},        # HTTP transport, OR
          "command": "...", "args": [...], "env": {...},  # STDIO transport
          "note": "...",
          "enabled": true
        }
      }
    }
"""

import json
import os
import re
import subprocess
import uuid

CONFIG_DIR = "/var/minis/mcp-servers"
CONFIG_PATH = os.path.join(CONFIG_DIR, "servers.json")
DEVICE_SERVER = "minis-device"
AUTOMATION_SERVER = "minis-automation"
AUTOMATION_URL = "http://127.0.0.1:8091/mcp"
AUTOMATION_BRIDGE = "/usr/local/bin/minis-automation-config"
RESERVED_SERVERS = frozenset((DEVICE_SERVER, AUTOMATION_SERVER))


def canonical_server_name(name):
    """Reserved local capabilities have one identity, irrespective of casing."""
    folded = name.casefold() if isinstance(name, str) else ""
    return folded if folded in RESERVED_SERVERS else name


def is_reserved_server(name):
    return canonical_server_name(name) in RESERVED_SERVERS if isinstance(name, str) else False


def is_automation_server(name):
    return canonical_server_name(name) == AUTOMATION_SERVER


def _without_reserved(servers):
    return {name: server for name, server in servers.items() if not is_reserved_server(name)}

# Startup / handshake timeout for a STDIO MCP server's first `initialize` RPC.
# This is a Minis server-configuration knob, NOT part of the MCP protocol (MCP
# only standardizes the JSON-RPC exchange after the client connects, not the
# client's local process-launch timeout). Slow-starting servers (e.g. a cold
# `uvx mcp-atlassian` that fetches packages) can take well over a minute before
# they answer `initialize`; the default gives them headroom, and the per-server
# field lets an operator extend it further.
DEFAULT_STARTUP_TIMEOUT = 60
# Upper bound so a fat-fingered value can't wedge a call thread indefinitely.
MAX_STARTUP_TIMEOUT = 900
# Primary field (highest priority) first, then compat aliases from other MCP
# clients, in descending priority. resolve_startup_timeout() reads these in
# order and uses the first present.
STARTUP_TIMEOUT_KEYS = (
    "startupTimeoutSeconds",   # Minis primary field (camelCase)
    "startup_timeout_sec",     # snake_case compat
    "startupTimeout",          # compat alias
    "handshakeTimeout",        # compat alias
    "initializeTimeout",       # compat alias
)


def _ensure_dir():
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
    except OSError:
        pass


def load_config():
    """Return the parsed config dict ({"mcpServers": {...}}). Empty on missing
    or unparseable file (callers treat that as "no servers")."""
    _ensure_dir()
    if not os.path.exists(CONFIG_PATH):
        return {"mcpServers": {}}
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {"mcpServers": {}}
    if not isinstance(data, dict) or not isinstance(data.get("mcpServers"), dict):
        return {"mcpServers": {}}
    return data


def save_config(config):
    """Write the config back atomically (write temp + rename) so a concurrent
    reader (Settings UI / agent) never sees a half-written file."""
    _ensure_dir()
    tmp = CONFIG_PATH + ".tmp"
    stored = dict(config)
    stored["mcpServers"] = _without_reserved(stored.get("mcpServers", {}))
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(stored, f, ensure_ascii=False, indent=2)
    os.replace(tmp, CONFIG_PATH)


def get_servers():
    """Return the mcpServers dict (name -> server object)."""
    servers = _without_reserved(load_config().get("mcpServers", {}))
    state = _local_device_state()
    # The local bridge is a reserved capability, never user-configurable
    # through servers.json. Drop a stale or forged entry before exposing it.
    if state:
        servers[DEVICE_SERVER] = {"enabled": True, "command": "/usr/bin/python3",
            "args": ["-I", "-S", "-B", "/usr/local/lib/minis-mcp-cli/device_server.py"],
            "note": "Device information and brightness with foreground approval"}
    automation = local_automation_config()
    if automation is not None:
        servers[AUTOMATION_SERVER] = automation
    return servers


def _strict_json(data):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate native config field")
            result[key] = value
        return result

    def reject_constant(_):
        raise ValueError("invalid native config number")

    return json.loads(data, object_pairs_hook=pairs, parse_constant=reject_constant)


def valid_automation_config(value):
    """Exact native bridge schema. No URL, headers or credentials from disk."""
    if type(value) is not dict or set(value) != {"enabled", "url", "headers", "session"}:
        return False
    if value["enabled"] is not True or value["url"] != AUTOMATION_URL:
        return False
    headers = value["headers"]
    if type(headers) is not dict or set(headers) != {"Authorization"}:
        return False
    bearer = headers["Authorization"]
    if type(bearer) is not str or re.fullmatch(r"Bearer [0-9a-f]{64}", bearer, re.ASCII) is None:
        return False
    session = value["session"]
    if type(session) is not str:
        return False
    try:
        identifier = uuid.UUID(session)
    except (ValueError, AttributeError):
        return False
    return identifier.version == 4 and str(identifier) == session


def local_automation_config():
    """Read ephemeral credentials through the fixed offload, never a file.

    Invalid, absent, disabled and expired native state all fail closed. Errors
    deliberately contain neither bridge output nor bearer header values.
    """
    if not os.path.isfile(AUTOMATION_BRIDGE):
        return None
    try:
        reply = subprocess.run([AUTOMATION_BRIDGE], stdout=subprocess.PIPE,
                               stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                               timeout=2, check=False)
        if reply.returncode != 0 or len(reply.stdout) > 2048:
            return None
        value = _strict_json(reply.stdout)
        return value if valid_automation_config(value) else None
    except (OSError, ValueError, TypeError, RecursionError, subprocess.SubprocessError):
        return None


def public_server_config(name, server):
    """Config display is not a route for ephemeral automation credentials."""
    if not is_automation_server(name):
        return server
    return {"enabled": True, "url": AUTOMATION_URL, "session": server.get("session"),
            "authentication": "native automation session (credential hidden)"}


def _local_device_state():
    bridge = "/usr/local/bin/minis-device-call"
    if not os.path.isfile(bridge):
        return None
    try:
        reply = subprocess.run([bridge, "status"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                               stdin=subprocess.DEVNULL, timeout=2, check=False)
        return reply.returncode == 0 and json.loads(reply.stdout) == {"enabled": True}
    except (OSError, ValueError, subprocess.SubprocessError):
        return False


def get_server(name):
    """Return one server object or None."""
    return get_servers().get(canonical_server_name(name))


def upsert_server(name, server):
    """Add or overwrite a server (last-write-wins on name collision)."""
    if is_reserved_server(name):
        return False
    config = load_config()
    config.setdefault("mcpServers", {})[name] = server
    save_config(config)


def remove_server(name):
    """Delete a server. Returns True if it existed."""
    if is_reserved_server(name):
        return False
    config = load_config()
    servers = config.setdefault("mcpServers", {})
    if name in servers:
        del servers[name]
        save_config(config)
        return True
    return False


def set_enabled(name, enabled):
    """Flip a server's enabled flag. Returns True if the server existed."""
    if is_reserved_server(name):
        return False
    config = load_config()
    server = config.get("mcpServers", {}).get(name)
    if server is None:
        return False
    server["enabled"] = bool(enabled)
    save_config(config)
    return True


def is_http(server):
    return bool(server.get("url"))


def is_stdio(server):
    return bool(server.get("command"))


def resolve_startup_timeout(server):
    """Resolve a STDIO server's startup/handshake timeout in seconds.

    Reads STARTUP_TIMEOUT_KEYS in priority order and returns the first present
    value, coerced to a positive int within [1, MAX_STARTUP_TIMEOUT]. Missing
    → DEFAULT_STARTUP_TIMEOUT with no warning.

    Never silently swallows a bad value: a non-numeric / zero / negative /
    over-limit value falls back to the default AND returns a human/AI-readable
    warning naming the field, the given value, the accepted range, and the
    value actually used, so a misconfiguration is self-correctable.

    Returns (timeout:int, warning:str|None).
    """
    server = server or {}
    for key in STARTUP_TIMEOUT_KEYS:
        if key not in server:
            continue
        raw = server[key]
        # bool is an int subclass; treat true/false as invalid, not 1/0.
        if isinstance(raw, bool):
            return DEFAULT_STARTUP_TIMEOUT, _timeout_warning(key, raw)
        # Accept ints and integer-valued numeric strings only. A fractional
        # value (1.5, "1.5") is a misconfiguration, not silently truncated.
        try:
            fvalue = float(raw)
        except (TypeError, ValueError):
            return DEFAULT_STARTUP_TIMEOUT, _timeout_warning(key, raw)
        if fvalue != int(fvalue):
            return DEFAULT_STARTUP_TIMEOUT, _timeout_warning(key, raw)
        value = int(fvalue)
        if value < 1 or value > MAX_STARTUP_TIMEOUT:
            return DEFAULT_STARTUP_TIMEOUT, _timeout_warning(key, raw)
        return value, None
    return DEFAULT_STARTUP_TIMEOUT, None


MAX_STDIO_SERVERS_ENV = "MINIS_MCP_MAX_STDIO_SERVERS"
MAX_STDIO_SERVERS_LIMIT = 16


def resolve_max_stdio_servers(default, env=None):
    """[T-mcp-pool-stdio-cap] How many STDIO servers the daemon keeps alive at
    once. Returns (value, warning).

    Read from the environment (Minis Settings > Environment Variables reach the
    guest shell, and the daemon inherits the env of the CLI that forked it), not
    from servers.json: the app rewrites servers.json with only `mcpServers`, so
    any extra top-level key would be dropped on the next edit. An unset, empty
    or invalid value falls back to `default`; invalid ones come with a warning.
    """
    raw = (os.environ if env is None else env).get(MAX_STDIO_SERVERS_ENV)
    if raw is None or str(raw).strip() == "":
        return default, None
    try:
        value = int(str(raw).strip())
    except ValueError:
        return default, "%s=%r is not an integer; using %d" % (MAX_STDIO_SERVERS_ENV, raw, default)
    if value < 1 or value > MAX_STDIO_SERVERS_LIMIT:
        return default, "%s=%d is outside 1..%d; using %d" % (
            MAX_STDIO_SERVERS_ENV, value, MAX_STDIO_SERVERS_LIMIT, default)
    return value, None


def _timeout_warning(field, given):
    return (
        "ignoring %s=%r: startup timeout must be an integer number of seconds "
        "in [1, %d]; using default %ds instead"
        % (field, given, MAX_STARTUP_TIMEOUT, DEFAULT_STARTUP_TIMEOUT)
    )
