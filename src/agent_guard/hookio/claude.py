"""Claude Code hooks (also used by DSH's dsh-hooks-claude-code plugin).

Input: {hook_event_name, session_id, cwd, tool_name, tool_input, ...}
Output: {"systemMessage": user text, "hookSpecificOutput": {"hookEventName", "additionalContext"}}
We never return a permission decision: warn-only.
"""

from __future__ import annotations

from agent_guard.hookio import HookInput, HookOutput, dumps, edit_text, file_arg

EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
SHELL_TOOLS = {"Bash"}


def classify(raw_event: str, tool: str) -> str:
    if raw_event == "SessionStart":
        return "session_start"
    if raw_event in ("Stop", "SubagentStop"):
        return "stop"
    if raw_event == "PreToolUse":
        return "pre_command" if tool in SHELL_TOOLS else "ignore"
    if raw_event == "PostToolUse":
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


EVENT_NAMES = {"session_start": "SessionStart", "post_command": "PostToolUse", "post_edit": "PostToolUse"}


def render(inp: HookInput, out: HookOutput) -> str:
    obj: dict = {}
    if out.user_text:
        obj["systemMessage"] = out.user_text
    if out.agent_text and inp.event in EVENT_NAMES:
        obj["hookSpecificOutput"] = {"hookEventName": inp.raw_event or EVENT_NAMES[inp.event],
                                     "additionalContext": out.agent_text}
    return dumps(obj) if obj else ""


def neutral(event_hint: str = "auto") -> str:
    return ""
