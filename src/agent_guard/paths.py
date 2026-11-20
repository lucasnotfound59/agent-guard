"""Where things live. Every location can be redirected with an env var so tests
never touch the real home directory."""

from __future__ import annotations

import os
from pathlib import Path

PKG_DIR = Path(__file__).resolve().parent


def repo_root() -> Path:
    """The regulation repo (rules, adapters, skills, global instructions)."""
    return Path(os.environ.get("AGENT_GUARD_REPO") or PKG_DIR.parent.parent)


def user_home() -> Path:
    return Path(os.environ.get("AGENT_GUARD_USER_HOME") or Path.home())


def state_home() -> Path:
    """Runtime state: venv, bin, git hooks, logs, backups. Never committed."""
    return Path(os.environ.get("AGENT_GUARD_HOME") or user_home() / ".agent-guard")


def guard_bin() -> Path:
    return state_home() / "bin" / "guard"


def git_hooks_dir() -> Path:
    return state_home() / "git-hooks"


def log_file() -> Path:
    return state_home() / "log" / "events.jsonl"


def private_terms_file() -> Path:
    """The user's own name, username and private e-mails, one per line. Never committed (it lives outside
    the repo), so rules can match personal details without publishing them."""
    return state_home() / "private-terms.txt"


def session_state_dir() -> Path:
    return state_home() / "state"


def expand(p: str | os.PathLike) -> Path:
    """Expand ~ against the (possibly redirected) user home."""
    s = str(p)
    if s == "~" or s.startswith("~/"):
        return user_home() / s[2:]
    return Path(s)


def contract(p: str | os.PathLike) -> str:
    """Show a path with ~ for the user home."""
    s = str(p)
    home = str(user_home())
    if s == home:
        return "~"
    if s.startswith(home + os.sep):
        return "~/" + s[len(home) + 1 :]
    return s
