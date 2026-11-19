"""Plain contract for plugins we write ourselves (OpenCode) and for future agents.

Input:  {"event": "post_edit", "cwd": "...", "session_id": "...",
         "command": "...", "files": ["..."], "edits": {"path": "new text"}}
Output: {"agent": "text for the model", "user": "text for the user"}
"""

from __future__ import annotations

from agent_guard.hookio import HookInput, HookOutput, dumps


def parse(payload: dict, event_hint: str = "auto") -> HookInput:
    event = event_hint if event_hint not in ("", "auto") else str(payload.get("event") or "ignore")
    files = payload.get("files") or []
    edits = payload.get("edits") or {}
    return HookInput(
        event=event,
        cwd=str(payload.get("cwd") or ""),
        session_id=str(payload.get("session_id") or ""),
        command=payload.get("command") if isinstance(payload.get("command"), str) else None,
        files=[str(f) for f in files if isinstance(f, str)],
        edits={str(k): str(v) for k, v in edits.items()} if isinstance(edits, dict) else {},
        raw_event=event,
        raw=payload,
    )


def render(inp: HookInput, out: HookOutput) -> str:
    return dumps({"agent": out.agent_text, "user": out.user_text})


def neutral(event_hint: str = "auto") -> str:
    return dumps({"agent": "", "user": ""})
