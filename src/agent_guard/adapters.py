"""Adapters: one YAML file per agent saying where its instructions, skills and
hooks live. Adding support for a new agent normally means adding one file.

Hook writers know each agent's config dialect. Every hook entry we add runs
`~/.agent-guard/bin/guard hook ...`, which is also how we find our own entries
again (we never touch anyone else's).
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import yaml

from agent_guard.paths import expand, guard_bin, repo_root
from agent_guard.state import InstallState

MARK = ".agent-guard/bin/guard hook"


@dataclass
class Adapter:
    name: str
    title: str
    detect_binaries: list[str] = field(default_factory=list)
    detect_paths: list[str] = field(default_factory=list)
    always: bool = False
    instructions: list[str] = field(default_factory=list)
    skills_mode: str = "none"  # link | native | none
    skills_dirs: list[str] = field(default_factory=list)
    skills_exclude: list[str] = field(default_factory=list)
    hooks_writer: str = "none"
    hooks_file: str = ""
    notes: str = ""

    def detected(self) -> bool:
        if self.always:
            return True
        if any(shutil.which(b) for b in self.detect_binaries):
            return True
        return any(expand(p).exists() for p in self.detect_paths)

    @classmethod
    def from_file(cls, f: Path) -> "Adapter":
        d = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        det = d.get("detect") or {}
        sk = d.get("skills") or {}
        hk = d.get("hooks") or {}
        return cls(
            name=d["name"],
            title=d.get("title", d["name"]),
            detect_binaries=list(det.get("binaries") or []),
            detect_paths=list(det.get("paths") or []),
            always=bool(det.get("always")),
            instructions=list(d.get("instructions") or []),
            skills_mode=str(sk.get("mode", "none")),
            skills_dirs=list(sk.get("dirs") or []),
            skills_exclude=list(sk.get("exclude") or []),
            hooks_writer=str(hk.get("writer", "none")),
            hooks_file=str(hk.get("file", "")),
            notes=str(d.get("notes", "")).strip(),
        )


def load_all() -> list[Adapter]:
    return [Adapter.from_file(f) for f in sorted((repo_root() / "adapters").glob("*.yaml"))]


# ------------------------------------------------------------------ hooks


def cmd(agent: str, fmt: str, event: str) -> str:
    return f"{guard_bin()} hook --agent {agent} --format {fmt} --event {event}"


def is_ours(obj: Any) -> bool:
    return MARK in json.dumps(obj)


def _claude_style(agent: str, fmt: str, timeout_key: str, timeout: int, extra: dict | None = None) -> Callable:
    def h(event: str) -> dict:
        d = {"type": "command", "command": cmd(agent, fmt, event), timeout_key: timeout}
        d.update(extra or {})
        return d
    return h


def hook_spec(adapter: Adapter) -> dict[str, list[dict]]:
    """The hook groups we want for this adapter, keyed by the agent's event names."""
    a, w = adapter.name, adapter.hooks_writer
    if w == "claude-settings":
        h = _claude_style(a, "claude", "timeout", 15)
        return {
            "SessionStart": [{"hooks": [h("session_start")]}],
            "PreToolUse": [{"matcher": "Bash", "hooks": [h("pre_command")]}],
            "PostToolUse": [{"matcher": "Bash|Edit|Write|MultiEdit|NotebookEdit", "hooks": [h("auto")]}],
            "Stop": [{"hooks": [h("stop")]}],
        }
    if w == "codex-hooks":
        h = _claude_style(a, "codex", "timeout", 15)
        return {
            "SessionStart": [{"matcher": "startup|resume", "hooks": [h("session_start")]}],
            "PreToolUse": [{"matcher": "Bash", "hooks": [h("pre_command")]}],
            "PostToolUse": [{"matcher": "Bash|apply_patch|Edit|Write", "hooks": [h("auto")]}],
            "Stop": [{"hooks": [h("stop")]}],
        }
    if w == "gemini-settings":
        h = _claude_style(a, "gemini", "timeout", 15000, {"name": "agent-guard"})
        return {
            "SessionStart": [{"hooks": [h("session_start")]}],
            "BeforeTool": [{"matcher": "run_shell_command", "hooks": [h("pre_command")]}],
            "AfterTool": [{"matcher": "run_shell_command|write_file|replace", "hooks": [h("auto")]}],
            "AfterAgent": [{"hooks": [h("stop")]}],
        }
    if w == "cursor-hooks":
        return {
            "sessionStart": [{"command": cmd(a, "cursor", "session_start")}],
            "postToolUse": [{"command": cmd(a, "cursor", "post_command")}],
            "stop": [{"command": cmd(a, "cursor", "stop")}],
        }
    if w == "copilot-hooks":
        def c(event: str) -> dict:
            return {"type": "command", "bash": cmd(a, "copilot", event), "timeoutSec": 15}
        return {"sessionStart": [c("session_start")], "postToolUse": [c("post_command")]}
    return {}


def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8")
    return json.loads(text) if text.strip() else {}


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _merge(hooks: dict, spec: dict) -> dict:
    for event, groups in spec.items():
        kept = [g for g in hooks.get(event) or [] if not is_ours(g)]
        hooks[event] = kept + groups
    return hooks


def _strip(hooks: dict) -> dict:
    for event in list(hooks):
        kept = [g for g in hooks[event] or [] if not is_ours(g)]
        if kept:
            hooks[event] = kept
        else:
            del hooks[event]
    return hooks


OWNED_FILE_WRITERS = {"copilot-hooks", "opencode-plugin"}


def install_hooks(adapter: Adapter, state: InstallState) -> str | None:
    w = adapter.hooks_writer
    if w == "none" or not adapter.hooks_file:
        return None
    path = expand(adapter.hooks_file)
    state.record(path)
    if w == "opencode-plugin":
        tpl = (repo_root() / "templates" / "opencode-plugin.js").read_text(encoding="utf-8")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(tpl.replace("__GUARD_BIN__", str(guard_bin())).replace("__AGENT__", adapter.name),
                        encoding="utf-8")
        return f"wrote plugin {path}"
    spec = hook_spec(adapter)
    if w == "copilot-hooks":
        _write_json(path, {"version": 1, "hooks": spec})
        return f"wrote {path}"
    doc = _read_json(path)
    if w == "cursor-hooks":
        doc.setdefault("version", 1)
    doc["hooks"] = _merge(doc.get("hooks") or {}, spec)
    _write_json(path, doc)
    return f"merged hooks into {path}"


def uninstall_hooks(adapter: Adapter, state: InstallState) -> str | None:
    w = adapter.hooks_writer
    if w == "none" or not adapter.hooks_file:
        return None
    path = expand(adapter.hooks_file)
    orig = state.originals.get(str(path))
    if w in OWNED_FILE_WRITERS:
        if orig:
            return state.restore(str(path))
        if path.exists():
            path.unlink()
        return f"removed {path}"
    if not path.exists():
        return None
    doc = _read_json(path)
    hooks = _strip(doc.get("hooks") or {})
    if hooks:
        doc["hooks"] = hooks
    else:
        doc.pop("hooks", None)
    if orig:
        # If nothing else changed since install, put back the exact original bytes (or remove our file).
        if orig["type"] == "missing" and doc in ({}, {"version": 1}):
            return state.restore(str(path))
        if orig["type"] == "file" and doc == _read_json(Path(orig["backup"])):
            return state.restore(str(path))
    _write_json(path, doc)
    return f"removed agent-guard hooks from {path}"


def hooks_present(adapter: Adapter) -> bool | None:
    w = adapter.hooks_writer
    if w == "none" or not adapter.hooks_file:
        return None
    path = expand(adapter.hooks_file)
    if not path.exists():
        return False
    if w == "opencode-plugin":
        return str(guard_bin()) in path.read_text(encoding="utf-8")
    try:
        hooks = _read_json(path).get("hooks") or {}
    except json.JSONDecodeError:
        return False
    return all(any(is_ours(g) for g in hooks.get(ev) or []) for ev in hook_spec(adapter))
