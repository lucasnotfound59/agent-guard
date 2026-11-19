"""GitHub Copilot CLI hooks (~/.copilot/hooks/*.json, version 1).

Input uses camelCase: {toolName, toolArgs (JSON string or object), cwd, ...}.
sessionStart and postToolUse accept {"additionalContext": ...}.
"""

from __future__ import annotations

import json

from agent_guard.hookio import HookInput, HookOutput, dumps, edit_text, file_arg

SHELL_TOOLS = {"bash", "shell", "powershell"}
EDIT_TOOLS = {"edit", "create", "write", "str_replace", "apply_patch", "str_replace_editor"}


def parse(payload: dict, event_hint: str = "auto") -> HookInput:
    tool = str(payload.get("toolName") or payload.get("tool_name") or "").lower()
    args = payload.get("toolArgs") or payload.get("tool_input") or {}
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except json.JSONDecodeError:
            args = {"command": args} if tool in SHELL_TOOLS else {}
    raw_event = str(payload.get("hookEventName") or payload.get("hook_event_name") or "")
    event = event_hint
    if event in ("", "auto", "post_command"):
        if raw_event.lower() == "sessionstart":
            event = "session_start"
        elif tool in SHELL_TOOLS:
            event = "post_command"
        elif tool in EDIT_TOOLS:
            event = "post_edit"
        elif event in ("", "auto"):
            event = "ignore"
    inp = HookInput(event=event, cwd=str(payload.get("cwd") or ""),
                    session_id=str(payload.get("sessionId") or payload.get("session_id") or ""),
                    raw_event=raw_event, raw=payload)
    if isinstance(args, dict):
        if isinstance(args.get("command"), str):
            inp.command = args["command"]
        f = file_arg(args)
        if f:
            inp.files.append(f)
            text = edit_text(args)
            if text is not None:
                inp.edits[f] = text
    return inp


def render(inp: HookInput, out: HookOutput) -> str:
    if out.agent_text and inp.event in ("session_start", "post_command", "post_edit"):
        return dumps({"additionalContext": out.agent_text})
    return "{}"


def neutral(event_hint: str = "auto") -> str:
    return "{}"
