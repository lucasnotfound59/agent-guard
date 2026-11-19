"""Translate each agent's hook payloads to one shape, and our answer back.

Every agent speaks a slightly different hook dialect. A format module exposes:
    parse(payload: dict, event_hint: str) -> HookInput
    render(inp: HookInput, out: HookOutput) -> str   (stdout; "" for nothing)
    neutral(event_hint: str) -> str                  (safe output if we crash)
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from importlib import import_module
from typing import Any

EVENTS = ("session_start", "pre_command", "post_command", "post_edit", "stop", "ignore")
FORMATS = ("claude", "codex", "gemini", "cursor", "copilot", "generic")


@dataclass
class HookInput:
    event: str
    cwd: str = ""
    session_id: str = ""
    command: str | None = None
    files: list[str] = field(default_factory=list)
    edits: dict[str, str] = field(default_factory=dict)  # path -> new text, when the payload carries it
    raw_event: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class HookOutput:
    agent_text: str = ""
    user_text: str = ""


def get(fmt: str):
    if fmt not in FORMATS:
        raise ValueError(f"unknown hook format {fmt!r}; known: {', '.join(FORMATS)}")
    return import_module(f"agent_guard.hookio.{fmt}")


def dumps(obj: dict[str, Any]) -> str:
    return json.dumps(obj, ensure_ascii=False)


def first(d: dict[str, Any], *keys: str) -> Any:
    for k in keys:
        v = d.get(k)
        if v not in (None, ""):
            return v
    return None


def edit_text(tool_input: dict[str, Any]) -> str | None:
    """New text an edit tool is writing, across the common argument names."""
    parts: list[str] = []
    for k in ("content", "new_string", "newString", "new_str", "file_text", "new_source", "text"):
        v = tool_input.get(k)
        if isinstance(v, str):
            parts.append(v)
    for e in tool_input.get("edits") or []:
        if isinstance(e, dict):
            v = first(e, "new_string", "newString", "new_text")
            if isinstance(v, str):
                parts.append(v)
    return "\n".join(parts) if parts else None


def file_arg(tool_input: dict[str, Any]) -> str | None:
    v = first(tool_input, "file_path", "filePath", "path", "target_file", "notebook_path", "filename")
    return v if isinstance(v, str) else None
