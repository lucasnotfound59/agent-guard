"""Gemini CLI hooks (~/.gemini/settings.json). stdout must be JSON only."""

from __future__ import annotations

from agent_guard.hookio import HookInput, HookOutput, dumps, edit_text, file_arg

EDIT_TOOLS = {"write_file", "replace", "edit", "smart_edit", "write_todos_file"}
SHELL_TOOLS = {"run_shell_command", "shell"}


def classify(raw_event: str, tool: str) -> str:
    if raw_event == "SessionStart":
        return "session_start"
    if raw_event == "AfterAgent":
        return "stop"
    if raw_event == "BeforeTool":
        return "pre_command" if tool in SHELL_TOOLS else "ignore"
    if raw_event == "AfterTool":
        if tool in SHELL_TOOLS:
            return "post_command"
        if tool in EDIT_TOOLS:
            return "post_edit"
    return "ignore"


def parse(payload: dict, event_hint: str = "auto") -> HookInput:
    raw_event = str(payload.get("hook_event_name") or "")
    tool = str(payload.get("tool_name") or "")
    ti = payload.get("tool_input") or {}
    event = event_hint if event_hint not in ("", "auto") else classify(raw_event, tool)
    inp = HookInput(event=event, cwd=str(payload.get("cwd") or ""), session_id=str(payload.get("session_id") or ""),
                    raw_event=raw_event, raw=payload)
    if isinstance(ti, dict):
        if isinstance(ti.get("command"), str):
            inp.command = ti["command"]
        f = file_arg(ti)
        if f:
            inp.files.append(f)
            text = edit_text(ti)
            if text is not None:
                inp.edits[f] = text
    return inp


EVENT_NAMES = {"session_start": "SessionStart", "post_command": "AfterTool", "post_edit": "AfterTool"}


def render(inp: HookInput, out: HookOutput) -> str:
    obj: dict = {}
    if out.user_text:
        obj["systemMessage"] = out.user_text
    if out.agent_text and inp.event in EVENT_NAMES:
        obj["hookSpecificOutput"] = {"hookEventName": EVENT_NAMES[inp.event], "additionalContext": out.agent_text}
    return dumps(obj)


def neutral(event_hint: str = "auto") -> str:
    return "{}"
