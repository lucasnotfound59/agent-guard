"""Global settings from config.yaml in the regulation repo."""

from __future__ import annotations

import copy
import os
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
    # Repos the global git hooks leave alone: throwaway repos that test suites create and commit into.
    "ignore_repos": ["**/pytest-of-*/**"],
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


FIND_TIMEOUT = 30


def scan_repos(limit: int = 200) -> tuple[list[Path], list[str]]:
    """Git repos under scan_roots (up to 4 levels deep), and the roots whose scan timed out.

    Stops descending at node_modules, .worktrees and every .git it finds (walking .git/objects made a cold scan
    of a big folder hit the timeout). A timed-out scan keeps what it found instead of returning nothing.
    """
    found: list[Path] = []
    timed_out: list[str] = []
    for root in load()["scan_roots"]:
        base = expand(root)
        if not base.is_dir():
            continue
        cmd = ["find", str(base), "-maxdepth", "4", "(", "-name", "node_modules", "-o", "-name", ".worktrees", ")",
               "-prune", "-o", "-name", ".git", "-type", "d", "-print", "-prune"]
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=FIND_TIMEOUT).stdout
        except subprocess.TimeoutExpired as e:
            timed_out.append(str(root))
            partial = e.stdout or ""
            out = partial.decode(errors="replace") if isinstance(partial, bytes) else partial
        # Every printed path ends in /.git, so the endswith check also drops a half-written last line.
        found += [Path(p).parent for p in out.splitlines() if p.endswith("/.git")]
    return sorted(set(found))[:limit], timed_out


def find_repos(limit: int = 200) -> list[Path]:
    return scan_repos(limit)[0]


def ignored_repo(root: Path) -> bool:
    """True for repos the global git hooks skip (config ignore_repos, matched against the absolute path).

    AGENT_GUARD_CHECK_TEST_REPOS=1 turns the skip off (agent-guard's own tests commit in pytest's temp dirs).
    It can only add checks, never skip more, so it is no way around them.
    """
    if os.environ.get("AGENT_GUARD_CHECK_TEST_REPOS") == "1":
        return False
    from agent_guard.rules import path_matches

    return path_matches(str(root), [str(p) for p in load()["ignore_repos"] or []])
