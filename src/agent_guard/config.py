"""Global settings from config.yaml in the regulation repo."""

from __future__ import annotations

import copy
import subprocess
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from agent_guard.paths import expand, repo_root

DEFAULTS: dict[str, Any] = {
    "mode": "warn",  # warn | enforce (enforce is reserved; nothing blocks today)
    "notify": {"enabled": True, "debounce_seconds": 300},
    "scan_roots": ["~/Documents"],
    "post_command_max_files": 200,
    "project_check_timeout": 60,
    "cursor_stop_followup": False,
    "doctor_interval_hours": 24,
}


def _merge(base: dict, extra: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (extra or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


@lru_cache(maxsize=1)
def load() -> dict[str, Any]:
    path = repo_root() / "config.yaml"
    data: dict = {}
    if path.exists():
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return _merge(DEFAULTS, data)


def find_repos(limit: int = 200) -> list[Path]:
    """Git repos under scan_roots (up to 4 levels deep, skipping node_modules and worktrees)."""
    found: list[Path] = []
    for root in load()["scan_roots"]:
        base = expand(root)
        if not base.is_dir():
            continue
        try:
            r = subprocess.run(["find", str(base), "-maxdepth", "4", "-name", ".git", "-type", "d",
                                "-not", "-path", "*/node_modules/*", "-not", "-path", "*/.worktrees/*"],
                               capture_output=True, text=True, timeout=30)
        except subprocess.TimeoutExpired:
            continue
        found += [Path(p).parent for p in r.stdout.splitlines() if p]
    return sorted(set(found))[:limit]
