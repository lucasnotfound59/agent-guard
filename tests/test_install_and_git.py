"""Install into a fake home, check every agent is wired, commit through the global hook, then uninstall
and check everything is back exactly as it was."""

import json
import os
import subprocess
import time
from pathlib import Path

from agent_guard import install
from agent_guard.events import read_log
from agent_guard.paths import repo_root
from tests.conftest import git

CLAUDE_SETTINGS = '{\n  "env": {},\n  "hooks": {\n    "Stop": [{"hooks": [{"type": "command", "command": "say done"}]}]\n  }\n}'


def make_skill(d: Path, body: str, mtime: float | None = None) -> None:
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(f"---\nname: {d.name}\ndescription: x\n---\n{body}\n")
    if mtime:
        os.utime(d / "SKILL.md", (mtime, mtime))


def seed(home: Path) -> None:
    (home / ".claude").mkdir()
    (home / ".claude/settings.json").write_text(CLAUDE_SETTINGS)
    (home / ".codex").mkdir()
    (home / ".codex/AGENTS.md").write_text("")
    (home / ".cursor").mkdir()
    (home / ".copilot").mkdir()
    old, new = time.time() - 86400 * 60, time.time() - 86400 * 10
    make_skill(home / ".claude/skills/manager", "claude copy")
    make_skill(home / ".codex/skills/manager", "codex copy")
    make_skill(home / ".codex/skills/playwright", "old playwright", old)
    make_skill(home / ".claude/skills/playwright", "new playwright", new)
    make_skill(home / ".agents/skills/html-video", "pool skill")
    os.symlink(home / ".agents/skills/html-video", home / ".codex/skills/html-video")  # duplicate for a native reader


def snapshot(home: Path) -> dict:
    snap = {}
    for p in sorted(home.rglob("*")):
        rel = str(p.relative_to(home))
        if rel.startswith(".agent-guard") or rel == ".gitconfig":
            continue
        if p.is_symlink():
            snap[rel] = "->" + os.readlink(p)
        elif p.is_file():
            snap[rel] = p.read_bytes()
        else:
            snap[rel] = "dir"
    return snap


def test_install_wires_everything_and_uninstall_restores(isolated):
    home = isolated
    seed(home)
    before = snapshot(home)
    install.install()
    target = repo_root() / "global" / "AGENTS.md"

    for p in (".claude/CLAUDE.md", ".codex/AGENTS.md", ".agents/AGENTS.md", ".copilot/copilot-instructions.md"):
        assert Path(os.readlink(home / p)) == target, p
    settings = json.loads((home / ".claude/settings.json").read_text())
    assert settings["env"] == {}
    stop_cmds = [h["command"] for g in settings["hooks"]["Stop"] for h in g["hooks"]]
    assert "say done" in stop_cmds and any("guard hook --agent claude" in c for c in stop_cmds)
    assert "PostToolUse" in settings["hooks"] and "SessionStart" in settings["hooks"]
    cursor = json.loads((home / ".cursor/hooks.json").read_text())
    assert cursor["version"] == 1 and "postToolUse" in cursor["hooks"]
    assert json.loads((home / ".copilot/hooks/agent-guard.json").read_text())["version"] == 1

    pool = home / ".agents/skills"
    assert Path(os.readlink(pool / "manager")) == repo_root() / "skills/manager"
    assert "new playwright" in (pool / "playwright/SKILL.md").read_text()
    assert Path(os.readlink(home / ".claude/skills/playwright")) == pool / "playwright"
    assert not (home / ".codex/skills/manager").exists()  # Codex reads the pool itself
    assert not (home / ".codex/skills/html-video").is_symlink()  # duplicate link removed

    hooks_path = subprocess.run(["git", "config", "--global", "core.hooksPath"], capture_output=True, text=True)
    assert hooks_path.stdout.strip() == str(home / ".agent-guard/git-hooks")

    mtime = (home / ".claude/settings.json").stat().st_mtime_ns
    dry = install.install(dry_run=True)
    hook_lines = [line for line in dry if "hooks" in line or "plugin" in line]
    assert hook_lines and all("up to date" in line for line in hook_lines), hook_lines
    install.install()  # idempotent
    settings2 = json.loads((home / ".claude/settings.json").read_text())
    assert len(settings2["hooks"]["Stop"]) == 2
    assert (home / ".claude/settings.json").stat().st_mtime_ns == mtime  # unchanged files aren't rewritten

    install.uninstall()
    assert snapshot(home) == before
    hooks_path = subprocess.run(["git", "config", "--global", "core.hooksPath"], capture_output=True, text=True)
    assert hooks_path.stdout.strip() == ""


def test_uninstall_keeps_later_user_edits(isolated):
    home = isolated
    seed(home)
    install.install()
    s = json.loads((home / ".claude/settings.json").read_text())
    s["model"] = "opus"
    (home / ".claude/settings.json").write_text(json.dumps(s))
    install.uninstall()
    s = json.loads((home / ".claude/settings.json").read_text())
    assert s["model"] == "opus" and "guard hook" not in json.dumps(s)


def test_commit_through_global_hook(isolated, repo):
    install.install()
    (repo / "app.py").write_text('DATA = "/Users/someone/x.csv"\n')
    git(repo, "add", "app.py")
    r = git(repo, "commit", "-m", "update stuff", check=False)
    assert r.returncode == 0, r.stderr  # warn-only: the commit still happens
    assert "no-hardcoded-home-path" in r.stderr and "conventional-commits" in r.stderr
    assert "git commit --amend" in r.stderr  # commit messages can't carry an allow comment
    kinds = {(e.get("kind"), e.get("rule")) for e in read_log() if e["event"] == "finding"}
    assert ("commit", "no-hardcoded-home-path") in kinds and ("commit-message", "conventional-commits") in kinds


def test_repo_own_hooks_still_run_and_keep_their_exit_code(isolated, repo):
    install.install()
    hooks = repo / ".git/hooks"
    (hooks / "post-commit").write_text("#!/bin/sh\ntouch \"$(git rev-parse --show-toplevel)/.post-commit-ran\"\n")
    (hooks / "post-commit").chmod(0o755)
    (repo / "a.txt").write_text("a\n")
    git(repo, "add", "a.txt")
    git(repo, "commit", "-q", "-m", "feat: a")
    assert (repo / ".post-commit-ran").exists()

    (hooks / "pre-commit").write_text("#!/bin/sh\necho repo hook says no >&2\nexit 1\n")
    (hooks / "pre-commit").chmod(0o755)
    (repo / "b.txt").write_text("b\n")
    git(repo, "add", "b.txt")
    r = git(repo, "commit", "-m", "feat: b", check=False)
    assert r.returncode != 0 and "repo hook says no" in r.stderr


def test_dry_run_changes_nothing(isolated):
    home = isolated
    seed(home)
    before = snapshot(home)
    log = install.install(dry_run=True)
    assert any("would add hooks to ~/.claude/settings.json" in line for line in log), log
    assert snapshot(home) == before


def test_settings_that_are_not_plain_json_are_left_alone(isolated, monkeypatch):
    home = isolated
    # The installer detects Gemini via its executable on PATH.
    bin_dir = home / "bin"
    bin_dir.mkdir()
    gemini = bin_dir / "gemini"
    gemini.write_text("#!/bin/sh\nexit 0\n")
    gemini.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir), prepend=os.pathsep)
    (home / ".gemini").mkdir()
    jsonc = '// my settings\n{"theme": "dark"}\n'
    (home / ".gemini/settings.json").write_text(jsonc)
    log = install.install()
    assert any("skipped: ~/.gemini/settings.json is not plain JSON" in line for line in log), log
    assert (home / ".gemini/settings.json").read_text() == jsonc
    install.uninstall()
    assert (home / ".gemini/settings.json").read_text() == jsonc


def test_reinstall_backs_up_what_replaced_our_links(isolated):
    """Editors and update tools often save by writing a new file over a symlink; reinstalling must not lose it."""
    home = isolated
    seed(home)
    install.install()
    md = home / ".claude/CLAUDE.md"
    md.unlink()
    md.write_text("my own notes\n")
    skill = home / ".claude/skills/playwright"
    skill.unlink()
    make_skill(skill, "playwright from an update tool")
    install.install()
    backups = home / ".agent-guard/backups"
    assert any(b.read_text() == "my own notes\n" for b in backups.rglob("CLAUDE.md"))
    assert any("from an update tool" in b.read_text() for b in backups.rglob("playwright/SKILL.md"))
    assert Path(os.readlink(md)) == repo_root() / "global/AGENTS.md"
    assert "from an update tool" in (home / ".agents/skills/playwright/SKILL.md").read_text()  # newer copy wins
