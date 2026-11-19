import os
import subprocess
from pathlib import Path

import pytest

from agent_guard.gitcmd import AGENT_ENV_HINTS


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """Every test gets a fake home, state dir and git global config. Nothing real is touched."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("AGENT_GUARD_USER_HOME", str(home))
    monkeypatch.setenv("AGENT_GUARD_HOME", str(home / ".agent-guard"))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(home / ".gitconfig"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("AGENT_GUARD_NOTIFY", "0")
    for var, _agent in AGENT_ENV_HINTS:  # tests run as "the user", even when an agent runs pytest
        monkeypatch.delenv(var, raising=False)
    (home / ".gitconfig").write_text("[user]\n\tname = Test\n\temail = test@example.com\n[init]\n\tdefaultBranch = main\n")
    return home


def git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=check)


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "work"
    r.mkdir()
    git(r, "init", "-q")
    (r / "README.md").write_text("hello\n")
    git(r, "add", ".")
    git(r, "-c", "core.hooksPath=/dev/null", "commit", "-q", "-m", "chore: init")
    return r
