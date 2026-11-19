"""Cursor hooks (~/.cursor/hooks.json, version 1).

The event name is passed on the command line (--event) because we register one
command per Cursor event. postToolUse can add `additional_context` for the
model; Cursor has no user-facing message there, so the user gets the
notification instead.
"""

from __future__ import annotations

import json

from agent_guard import config
from agent_guard.hookio import HookInput, HookOutput, dumps, edit_text, file_arg

SHELL_TOOLS = {"Shell", "shell", "run_terminal_cmd", "Terminal"}
EDIT_TOOLS = {"Write", "Edit", "StrReplace", "MultiEdit", "edit_file", "write", "search_replace", "Delete"}
RAW = {"sessionStart": "session_start", "postToolUse": "post_command", "stop": "stop", "afterFileEdit": "post_edit"}


def parse(payload: dict, event_hint: str = "auto") -> HookInput:
    raw_event = str(payload.get("hook_event_name") or "")
    hint = event_hint if event_hint not in ("", "auto") else RAW.get(raw_event, "ignore")
    tool = str(payload.get("tool_name") or "")
    ti = payload.get("tool_input") or {}
    if isinstance(ti, str):
        try:
            ti = json.loads(ti)
        except json.JSONDecodeError:
            ti = {}
    roots = payload.get("workspace_roots") or []
    cwd = str(payload.get("cwd") or (roots[0] if roots else ""))
    event = hint
    if hint == "post_command":  # postToolUse fires for every tool: decide by tool
        event = "post_command" if tool in SHELL_TOOLS else "post_edit" if tool in EDIT_TOOLS else "ignore"
    inp = HookInput(event=event, cwd=cwd, session_id=str(payload.get("conversation_id") or payload.get("session_id") or ""),
                    raw_event=raw_event or hint, raw=payload)
    if isinstance(ti, dict):
        if isinstance(ti.get("command"), str):
            inp.command = ti["command"]
        f = file_arg(ti) or (payload.get("file_path") if isinstance(payload.get("file_path"), str) else None)
        if f:
            inp.files.append(f)
            text = edit_text(ti)
            if text is not None:
                inp.edits[f] = text
    return inp


def render(inp: HookInput, out: HookOutput) -> str:
    if inp.event == "session_start":
        return dumps({"additional_context": out.agent_text}) if out.agent_text else "{}"
    if inp.event in ("post_command", "post_edit"):
        return dumps({"additional_context": out.agent_text}) if out.agent_text else "{}"
    if inp.event == "stop" and out.agent_text and config.load().get("cursor_stop_followup"):
        return dumps({"followup_message": out.agent_text})
    return "{}"


def neutral(event_hint: str = "auto") -> str:
    return "{}"
