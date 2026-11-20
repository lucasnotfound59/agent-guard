"""Where each agent keeps its user-level MCP servers, and how to read and write them.

Every agent has its own dialect, so servers are converted to one neutral shape:
    {"transport": "stdio" | "http" | "sse", "command": str, "args": [str], "env": {str: str}, "cwd": str,
     "url": str, "headers": {str: str}}
Fields the neutral shape doesn't know (timeouts, trust flags) stay in the agent's own entry and are dropped
only when a server is copied to another agent.

JSON files are rewritten whole (key order kept). Codex's TOML is edited as text, because the standard library
can read TOML but not write it, and every edit is parsed back and compared with what was intended before the
file is replaced, so a mistake can't corrupt the config.
"""

from __future__ import annotations

import copy
import json
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent_guard.paths import expand

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.@-]{0,63}$")


class ConfigError(ValueError):
    """The config can't be read or changed safely; the message says what to do."""


@dataclass(frozen=True)
class Target:
    agent: str     # adapter name
    path: str      # with ~
    fmt: str       # json | toml
    key: str       # where the servers live in the file
    dialect: str

    @property
    def file(self) -> Path:
        return expand(self.path)


TARGETS = [
    Target("claude", "~/.claude.json", "json", "mcpServers", "claude"),
    Target("codex", "~/.codex/config.toml", "toml", "mcp_servers", "codex"),
    Target("gemini", "~/.gemini/settings.json", "json", "mcpServers", "gemini"),
    Target("cursor", "~/.cursor/mcp.json", "json", "mcpServers", "standard"),
    Target("copilot", "~/.copilot/mcp-config.json", "json", "mcpServers", "copilot"),
    Target("qwen-code", "~/.qwen/settings.json", "json", "mcpServers", "gemini"),
    Target("opencode", "~/.config/opencode/opencode.json", "json", "mcp", "opencode"),
    Target("antigravity", "~/.gemini/antigravity/mcp_config.json", "json", "mcpServers", "antigravity"),
]
BY_AGENT = {t.agent: t for t in TARGETS}


# ------------------------------------------------------------------ neutral <-> native


def _strs(v: Any) -> list[str]:
    return [str(x) for x in v] if isinstance(v, list) else []


def _map(v: Any) -> dict[str, str]:
    return {str(k): str(x) for k, x in v.items()} if isinstance(v, dict) else {}


def to_neutral(dialect: str, d: Any) -> dict[str, Any]:
    d = d if isinstance(d, dict) else {}
    s: dict[str, Any] = {"transport": "stdio", "command": "", "args": [], "env": {}, "cwd": "", "url": "",
                         "headers": {}}
    if dialect == "opencode":
        cmd = _strs(d.get("command"))
        if d.get("type") == "remote" or d.get("url"):
            s.update(transport="http", url=str(d.get("url") or ""), headers=_map(d.get("headers")))
        else:
            s.update(command=cmd[0] if cmd else "", args=cmd[1:], env=_map(d.get("environment")))
        return s
    url = d.get("httpUrl") or d.get("url") or d.get("serverUrl") or ""
    if url:
        kind = str(d.get("type") or "")
        sse = kind == "sse" or (dialect in ("gemini", "standard", "antigravity") and "httpUrl" not in d
                                and str(url).rstrip("/").endswith("/sse"))
        s.update(transport="sse" if sse else "http", url=str(url),
                 headers=_map(d.get("http_headers") if dialect == "codex" else d.get("headers")))
    else:
        s.update(command=str(d.get("command") or ""), args=_strs(d.get("args")), env=_map(d.get("env")),
                 cwd=str(d.get("cwd") or ""))
    return s


def to_native(dialect: str, s: dict[str, Any]) -> dict[str, Any]:
    stdio = s.get("transport", "stdio") == "stdio"
    env, headers = dict(s.get("env") or {}), dict(s.get("headers") or {})
    if dialect == "opencode":
        if stdio:
            out: dict[str, Any] = {"type": "local", "command": [s["command"], *s.get("args", [])], "enabled": True}
            if env:
                out["environment"] = env
            return out
        out = {"type": "remote", "url": s["url"], "enabled": True}
        if headers:
            out["headers"] = headers
        return out
    if stdio:
        out = {"command": s["command"], "args": list(s.get("args") or [])}
        if dialect in ("claude", "copilot"):
            out = {"type": "stdio" if dialect == "claude" else "local", **out}
        if env:
            out["env"] = env
        if s.get("cwd") and dialect in ("codex", "gemini"):
            out["cwd"] = s["cwd"]
        if dialect == "copilot":
            out["tools"] = ["*"]
        return out
    sse = s.get("transport") == "sse"
    if dialect == "codex":
        out = {"url": s["url"]}
        if headers:
            out["http_headers"] = headers
        return out
    if dialect == "gemini":
        out = {"url" if sse else "httpUrl": s["url"]}
    elif dialect == "antigravity":
        out = {"serverUrl": s["url"]}
    elif dialect == "claude":
        out = {"type": "sse" if sse else "http", "url": s["url"]}
    elif dialect == "copilot":
        out = {"type": "sse" if sse else "http", "url": s["url"], "tools": ["*"]}
    else:
        out = {"url": s["url"]}
    if headers:
        out["headers"] = headers
    return out


def same_server(a: dict[str, Any], b: dict[str, Any]) -> bool:
    keys = ("transport", "command", "args", "env", "url", "headers")
    return all(a.get(k) == b.get(k) for k in keys)


def validate_neutral(s: dict[str, Any]) -> dict[str, Any]:
    """Clean a server typed into the dashboard, or raise ConfigError."""
    transport = s.get("transport")
    if transport not in ("stdio", "http", "sse"):
        raise ConfigError("transport must be stdio, http or sse")
    out = {"transport": transport, "command": "", "args": [], "env": {}, "cwd": "", "url": "", "headers": {}}
    for k in ("env", "headers"):
        v = s.get(k) or {}
        if not isinstance(v, dict) or not all(isinstance(x, str) and isinstance(y, str) for x, y in v.items()):
            raise ConfigError(f"{k} must map names to text")
        if any(not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]*", x) for x in v):
            raise ConfigError(f"{k} names may only use letters, digits, _ . and -")
        out[k] = dict(v)
    if transport == "stdio":
        command = str(s.get("command") or "").strip()
        args = s.get("args") or []
        if not command:
            raise ConfigError("a stdio server needs a command")
        if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
            raise ConfigError("args must be a list of text")
        out.update(command=command, args=list(args), cwd=str(s.get("cwd") or "").strip(), headers={})
    else:
        url = str(s.get("url") or "").strip()
        if not re.match(r"^https?://[^\s]+$", url):
            raise ConfigError("an http or sse server needs an http(s):// URL")
        out.update(url=url, env={})
    return out


# ------------------------------------------------------------------ reading


def read(t: Target) -> tuple[dict[str, Any] | None, str]:
    """(whole parsed file or None if missing, raw text). Raises ConfigError if unreadable."""
    try:
        raw = t.file.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None, ""
    except (OSError, UnicodeDecodeError) as e:
        raise ConfigError(f"can't read {t.path}: {type(e).__name__}") from None
    try:
        data = tomllib.loads(raw) if t.fmt == "toml" else json.loads(raw)
    except (tomllib.TOMLDecodeError, json.JSONDecodeError) as e:
        raise ConfigError(f"{t.path} isn't valid {t.fmt.upper()} ({e}); fix it by hand first") from None
    if not isinstance(data, dict):
        raise ConfigError(f"{t.path} isn't a {t.fmt.upper()} object")
    return data, raw


def servers(t: Target) -> dict[str, dict[str, Any]]:
    data, _ = read(t)
    found = (data or {}).get(t.key) or {}
    return {str(k): v for k, v in found.items() if isinstance(v, dict)} if isinstance(found, dict) else {}


# ------------------------------------------------------------------ writing


def updated_text(t: Target, change: dict[str, Any | None]) -> str:
    """The new file text after setting (or, with None, removing) these servers. Nothing is written here."""
    data, raw = read(t)
    if t.fmt == "json":
        new = copy.deepcopy(data) if data is not None else ({"$schema": "https://opencode.ai/config.json"}
                                                             if t.dialect == "opencode" else {})
        table = new.get(t.key)
        if table is None:
            table = new[t.key] = {}
        if not isinstance(table, dict):
            raise ConfigError(f"{t.key} in {t.path} isn't an object; fix it by hand first")
        for name, native in change.items():
            if native is None:
                table.pop(name, None)
            else:
                table[name] = native
        return json.dumps(new, indent=2, ensure_ascii=False) + "\n"
    return toml_set_tables(raw, (t.key,), change)


# ------------------------------------------------------------------ TOML text editing

_HEADER = re.compile(r"^\s*\[(\[?)\s*(.*?)\s*\]?\]\s*(?:#.*)?$")
_BARE = re.compile(r"^[A-Za-z0-9_-]+$")


def _key_path(text: str) -> tuple[str, ...] | None:
    """Split a dotted TOML key (`mcp_servers."my server".env`) into its parts."""
    parts, i = [], 0
    while i < len(text):
        while i < len(text) and text[i] in " \t":
            i += 1
        if i < len(text) and text[i] == '"':
            m = re.match(r'"(?:[^"\\]|\\.)*"', text[i:])
            if not m:
                return None
            parts.append(json.loads(m.group(0)))
            i += m.end()
        elif i < len(text) and text[i] == "'":
            j = text.find("'", i + 1)
            if j < 0:
                return None
            parts.append(text[i + 1 : j])
            i = j + 1
        else:
            m = re.match(r"[A-Za-z0-9_-]+", text[i:])
            if not m:
                return None
            parts.append(m.group(0))
            i += m.end()
        while i < len(text) and text[i] in " \t":
            i += 1
        if i < len(text):
            if text[i] != ".":
                return None
            i += 1
    return tuple(parts)


def _segments(lines: list[str]) -> list[tuple[tuple[str, ...] | None, int, int]]:
    """(table path, first line, end line) for the preamble and every [table] / [[array]] section."""
    out: list[tuple[tuple[str, ...] | None, int, int]] = []
    start, path = 0, None
    for i, line in enumerate(lines):
        m = _HEADER.match(line)
        if not m:
            continue
        out.append((path, start, i))
        path, start = _key_path(m.group(2)) or ("\0unparsed",), i
    out.append((path, start, len(lines)))
    return out


def toml_key(name: str) -> str:
    return name if _BARE.match(name) else json.dumps(name, ensure_ascii=False)


def toml_value(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, str):
        return json.dumps(v, ensure_ascii=False)  # JSON string escapes are valid TOML basic strings
    if isinstance(v, list):
        return "[" + ", ".join(toml_value(x) for x in v) + "]"
    if isinstance(v, dict):
        return "{ " + ", ".join(f"{toml_key(k)} = {toml_value(x)}" for k, x in v.items()) + " }"
    raise ConfigError(f"can't write {type(v).__name__} to TOML")


def _render_table(path: tuple[str, ...], value: dict[str, Any]) -> list[str]:
    head = ".".join(toml_key(p) for p in path)
    lines = [f"[{head}]"]
    subtables = []
    for k, v in value.items():
        if isinstance(v, dict) and v:
            subtables.append((k, v))
        else:
            lines.append(f"{toml_key(k)} = {toml_value(v)}")
    for k, v in subtables:
        lines.append("")
        lines += _render_table((*path, k), v)
    return lines


def toml_set_tables(raw: str, parent: tuple[str, ...], change: dict[str, Any | None]) -> str:
    """Replace, add or (value None) remove the tables parent.<name> in raw TOML text, verified by parsing."""
    try:
        before = tomllib.loads(raw) if raw else {}
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"the TOML isn't valid ({e}); fix it by hand first") from None
    lines = raw.splitlines()
    n = len(parent)
    doomed = {name for name in change}
    keep: list[str] = []
    for path, start, end in _segments(lines):
        if path is not None and len(path) > n and path[:n] == parent and path[n] in doomed:
            continue
        keep += lines[start:end]
    while keep and not keep[-1].strip():
        keep.pop()
    for name, value in change.items():
        if value is not None:
            keep += ["", *_render_table((*parent, name), value)]
    text = "\n".join(keep).lstrip("\n") + "\n"

    expected = copy.deepcopy(before)
    node = expected
    for p in parent:
        node = node.setdefault(p, {})
    for name, value in change.items():
        if value is None:
            node.pop(name, None)
        else:
            node[name] = value
    if not node and len(parent) and not _parent_had_header(lines, parent):
        _drop_empty(expected, parent)
    try:
        after = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"editing the TOML would break it ({e}); nothing was changed") from None
    if after != expected:
        raise ConfigError("this config defines those entries in a way the dashboard can't edit safely (for example "
                          "inline or dotted keys); change it by hand. Nothing was changed")
    return text


def _parent_had_header(lines: list[str], parent: tuple[str, ...]) -> bool:
    return any(path == parent for path, _, _ in _segments(lines))


def _drop_empty(d: dict[str, Any], parent: tuple[str, ...]) -> None:
    node = d
    for p in parent[:-1]:
        node = node.get(p, {})
    if node.get(parent[-1]) == {}:
        node.pop(parent[-1])


def toml_set_key(raw: str, table: tuple[str, ...], key: str, value: Any) -> str:
    """Set one key inside an existing [table] (e.g. plugins."x".enabled), verified by parsing."""
    before = tomllib.loads(raw)
    lines = raw.splitlines()
    for path, start, end in _segments(lines):
        if path != table:
            continue
        pat = re.compile(rf"^\s*{re.escape(toml_key(key))}\s*=")
        for i in range(start + 1, end):
            if pat.match(lines[i]):
                lines[i] = f"{toml_key(key)} = {toml_value(value)}"
                break
        else:
            lines.insert(start + 1, f"{toml_key(key)} = {toml_value(value)}")
        text = "\n".join(lines) + "\n"
        expected = copy.deepcopy(before)
        node = expected
        for p in table:
            node = node.setdefault(p, {})
        node[key] = value
        if tomllib.loads(text) != expected:
            raise ConfigError("couldn't change that setting safely; edit the file by hand. Nothing was changed")
        return text
    raise ConfigError(f"[{'.'.join(toml_key(p) for p in table)}] isn't a table the dashboard can edit; "
                      "change it by hand")
