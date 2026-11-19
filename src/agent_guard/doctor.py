"""Is every agent still wired up? Also spots agents that have no adapter yet."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from agent_guard import adapters as ad
from agent_guard import secrets, skills
from agent_guard.paths import contract, expand, git_hooks_dir, guard_bin, repo_root
from agent_guard.rules import global_rules, validate

# Agents we know about but may not have an adapter for yet: binaries and config dirs.
KNOWN_AGENTS: dict[str, dict[str, list[str]]] = {
    "aider": {"bin": ["aider"], "dirs": []},
    "goose": {"bin": ["goose"], "dirs": ["~/.config/goose"]},
    "amp": {"bin": ["amp"], "dirs": ["~/.config/amp"]},
    "qwen-code": {"bin": ["qwen"], "dirs": ["~/.qwen"]},
    "kiro": {"bin": ["kiro", "kiro-cli"], "dirs": ["~/.kiro"]},
    "crush": {"bin": ["crush"], "dirs": ["~/.config/crush"]},
    "factory-droid": {"bin": ["droid"], "dirs": ["~/.factory"]},
    "cursor-agent": {"bin": ["cursor-agent"], "dirs": []},
    "copilot-cli": {"bin": ["copilot"], "dirs": []},
    "kimi": {"bin": ["kimi"], "dirs": ["~/.kimi"]},
    "cline": {"bin": ["cline"], "dirs": ["~/.cline"]},
    "windsurf": {"bin": ["windsurf"], "dirs": ["~/.codeium/windsurf"]},
    "trae": {"bin": ["trae"], "dirs": ["~/.trae"]},
    "qoder": {"bin": ["qoder", "qodercli"], "dirs": ["~/.qoder"]},
    "augment": {"bin": ["auggie"], "dirs": ["~/.augment"]},
    "continue": {"bin": ["cn"], "dirs": ["~/.continue"]},
    "iflow": {"bin": ["iflow"], "dirs": ["~/.iflow"]},
    "junie": {"bin": ["junie"], "dirs": ["~/.junie"]},
    "pi": {"bin": [], "dirs": ["~/.pi"]},
}


def _global_hooks_path() -> str:
    r = subprocess.run(["git", "config", "--global", "--get", "core.hooksPath"], capture_output=True, text=True)
    return r.stdout.strip()


def results() -> list[tuple[str, str]]:
    """(level, message) with level in ok | warn | fail | info."""
    out: list[tuple[str, str]] = []
    add = out.append

    gb = guard_bin()
    add(("ok", f"guard runtime at {contract(gb)}") if os.access(gb, os.X_OK)
        else ("fail", f"guard runtime missing at {contract(gb)} (run ./install.sh)"))
    lb = expand("~/.local/bin/guard")
    add(("ok", "`guard` is on PATH via ~/.local/bin") if lb.is_symlink() and Path(os.readlink(lb)) == gb
        else ("warn", "~/.local/bin/guard is not linked to the runtime"))

    hp = _global_hooks_path()
    if hp and Path(os.path.expanduser(hp)) == git_hooks_dir():
        ok = all(os.access(git_hooks_dir() / h, os.X_OK) for h in ("pre-commit", "commit-msg"))
        add(("ok", "global git hooks active") if ok else ("fail", "git hook dispatcher missing in ~/.agent-guard/git-hooks"))
    else:
        add(("fail", f"git core.hooksPath is {hp or 'unset'}, expected {contract(git_hooks_dir())}"))

    rules = global_rules("active")
    bad = [f"{r.id}: {'; '.join(validate(r))}" for r in rules if validate(r)]
    add(("fail", "invalid rules: " + " | ".join(bad)) if bad else ("ok", f"{len(rules)} active rules valid"))
    cands = global_rules("candidate")
    if cands:
        add(("warn", f"{len(cands)} rule proposal(s) waiting for you: {', '.join(r.id for r in cands)} (run `guard review`)"))
    add(("ok", "gitleaks available for secret scanning") if secrets.available()
        else ("warn", "gitleaks not installed; secret checks use built-in patterns only (brew install gitleaks)"))

    target = repo_root() / "global" / "AGENTS.md"
    covered_bins: set[str] = set()
    covered_dirs: set[str] = set()
    for a in ad.load_all():
        covered_bins.update(a.detect_binaries)
        covered_dirs.update(str(expand(p)) for p in a.detect_paths)
        if not a.detected():
            add(("info", f"{a.title}: not installed, skipped"))
            continue
        for p in map(expand, a.instructions):
            if p.is_symlink() and Path(os.readlink(p)) == target:
                continue
            add(("fail", f"{a.title}: {contract(p)} does not point to the global AGENTS.md"))
        present = ad.hooks_present(a)
        if present is False:
            add(("fail", f"{a.title}: agent-guard hooks missing in {a.hooks_file}"))
        elif present:
            add(("ok", f"{a.title}: instructions + hooks wired"))
        else:
            add(("ok", f"{a.title}: instructions wired (no hook support)"))
        if a.notes:
            add(("info", f"{a.title}: {a.notes}"))
    out.extend(skills.status(ad.load_all()))

    for name, spec in KNOWN_AGENTS.items():
        bins = [b for b in spec["bin"] if b not in covered_bins and shutil.which(b)]
        dirs = [d for d in spec["dirs"] if str(expand(d)) not in covered_dirs and expand(d).exists()]
        if bins or dirs:
            found = ", ".join(bins + [contract(expand(d)) for d in dirs])
            add(("warn", f"unwired agent: {name} ({found}) — add adapters/{name}.yaml, then `guard install`"))
    return out


def quick_issues() -> list[str]:
    return [m for lvl, m in results() if lvl in ("fail", "warn") and "proposal" not in m]


def render(res: list[tuple[str, str]]) -> str:
    icon = {"ok": "✓", "warn": "!", "fail": "✗", "info": "·"}
    return "\n".join(f"{icon[lvl]} {msg}" for lvl, msg in res)
