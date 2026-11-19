"""Global settings from config.yaml in the regulation repo."""

from __future__ import annotations

import copy
from functools import lru_cache
from typing import Any

import yaml

from agent_guard.paths import repo_root

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
