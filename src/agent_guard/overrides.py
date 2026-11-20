"""Choices made in the dashboard's Toolbox that `guard install` must respect as well.

- skills_off: {agent: [skill, ...]}: skills turned off for one agent (on top of its adapter's exclude list)
- skills_parked: [skill, ...]: skills turned off for every agent (moved out of the pool into parked_skills_dir())
- mcp_parked: {agent: {server: definition}}: MCP servers switched off for an agent, kept here to switch back on

Lives in ~/.agent-guard/overrides.json, never committed, readable only by you: a parked MCP server can hold API keys.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from agent_guard.paths import state_home


def path() -> Path:
    return state_home() / "overrides.json"


def parked_skills_dir() -> Path:
    return state_home() / "parked-skills"


def load() -> dict[str, Any]:
    try:
        data = json.loads(path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    data["skills_off"] = {k: [str(s) for s in v] for k, v in (data.get("skills_off") or {}).items()
                          if isinstance(v, list)}
    data["skills_parked"] = [str(s) for s in data.get("skills_parked") or []]
    data["mcp_parked"] = {k: dict(v) for k, v in (data.get("mcp_parked") or {}).items() if isinstance(v, dict)}
    return data


def save(data: dict[str, Any]) -> None:
    p = path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".{p.name}.{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
    os.replace(tmp, p)


def skills_off(agent: str) -> list[str]:
    return load()["skills_off"].get(agent, [])


def parked_skills() -> set[str]:
    return set(load()["skills_parked"])
