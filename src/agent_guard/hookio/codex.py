"""Codex hooks (~/.codex/hooks.json). Same envelope as Claude Code, but file edits
arrive as tool_name "apply_patch" with the patch text in tool_input.command."""

from __future__ import annotations

from agent_guard.diff import parse_apply_patch
from agent_guard.hookio import HookInput, HookOutput, dumps, edit_text, file_arg

EDIT_TOOLS = {"apply_patch", "Edit", "Write"}
SHELL_TOOLS = {"Bash", "shell", "exec_command", "local_shell"}


def classify(raw_event: str, tool: str) -> str:
    if raw_event == "SessionStart":
        return "session_start"
    if raw_event in ("Stop", "SubagentStop"):
        return "stop"
    if raw_event == "PreToolUse":
        return "pre_command" if tool in SHELL_TOOLS else "ignore"
    if raw_event == "PostToolUse":
        if tool in EDIT_TOOLS:
            return "post_edit"
        if tool in SHELL_TOOLS:
            return "post_command"
    return "ignore"


def parse(payload: dict, event_hint: str = "auto") -> HookInput:
    raw_event = str(payload.get("hook_event_name") or "")
    tool = str(payload.get("tool_name") or "")
    ti = payload.get("tool_input") or {}
    event = event_hint if event_hint not in ("", "auto") else classify(raw_event, tool)
    if event == "post_command" and tool in EDIT_TOOLS:
        event = "post_edit"
    inp = HookInput(event=event, cwd=str(payload.get("cwd") or ""), session_id=str(payload.get("session_id") or ""),
                    raw_event=raw_event, raw=payload)
    if not isinstance(ti, dict):
        return inp
    cmd = ti.get("command")
    if isinstance(cmd, list):
        cmd = " ".join(str(c) for c in cmd)
    if tool == "apply_patch" and isinstance(cmd, str):
        paths, added = parse_apply_patch(cmd)
        inp.files.extend(paths)
        inp.edits.update({p: "\n".join(lines) for p, lines in added.items()})
    elif isinstance(cmd, str):
        inp.command = cmd
    f = file_arg(ti)
    if f and f not in inp.files:
        inp.files.append(f)
        text = edit_text(ti)
        if text is not None:
            inp.edits[f] = text
    return inp


EVENT_NAMES = {"session_start": "SessionStart", "post_command": "PostToolUse", "post_edit": "PostToolUse"}


def render(inp: HookInput, out: HookOutput) -> str:
    obj: dict = {}
    if out.user_text:
        obj["systemMessage"] = out.user_text
    if out.agent_text and inp.event in EVENT_NAMES:
        obj["hookSpecificOutput"] = {"hookEventName": EVENT_NAMES[inp.event], "additionalContext": out.agent_text}
    return dumps(obj) if obj else ""


def neutral(event_hint: str = "auto") -> str:
    return ""
