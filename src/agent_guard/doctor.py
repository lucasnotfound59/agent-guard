"""Is every agent still wired up? Also spots agents that have no adapter yet."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from agent_guard import adapters as ad
from agent_guard import config, events, secrets, skills
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


OPENCODE_LOG = "~/.local/share/opencode/log/opencode.log"


def opencode_plugin_error(plugin_file: Path) -> str | None:
    """The cause, if OpenCode's own log says the last attempt to load our plugin failed."""
    log = expand(OPENCODE_LOG)
    if not log.is_file():
        return None
    try:
        with log.open("rb") as fh:
            fh.seek(max(0, log.stat().st_size - 2_000_000))
            tail = fh.read().decode("utf-8", errors="replace").splitlines()
    except OSError:
        return None
    mine = [line for line in tail if str(plugin_file) in line and ("loading plugin" in line or "failed to load" in line)]
    if not mine or "failed to load" not in mine[-1]:
        return None
    cause = mine[-1].split("cause=", 1)[-1]
    return cause[:240]


def local_hook_overrides(repos: list[Path] | None = None) -> list[tuple[Path, str]]:
    """Repos under scan_roots whose own core.hooksPath (e.g. husky) replaces the global agent-guard hooks."""
    out: list[tuple[Path, str]] = []
    for repo in config.find_repos() if repos is None else repos:
        r = subprocess.run(["git", "-C", str(repo), "config", "--local", "--get", "core.hooksPath"],
                           capture_output=True, text=True)
        hp = r.stdout.strip()
        if hp and Path(os.path.expanduser(hp)) != git_hooks_dir():
            out.append((repo, hp))
    return out


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
    repos, timed_out = config.scan_repos()
    for root in timed_out:
        add(("warn", f"finding repos under {root} took over {config.FIND_TIMEOUT}s, so repo checks only cover the "
                     f"{len(repos)} found in time. Narrow scan_roots in config.yaml"))
    for repo, own in local_hook_overrides(repos):
        add(("warn", f"{contract(repo)} sets its own core.hooksPath ({own}), so agent-guard's commit checks don't run "
                     f"there. Add `{contract(gb)} git pre-commit` and `{contract(gb)} git commit-msg \"$1\"` to those "
                     "hooks, or remove the local setting"))

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

    seen = {r.get("agent") for r in events.read_log(_days_ago(30))}
    for a in ad.load_all():
        if not a.detected() or ad.hooks_present(a) is not True:
            continue
        if a.hooks_writer == "opencode-plugin":
            err = opencode_plugin_error(expand(a.hooks_file))
            if err:
                add(("fail", f"{a.title}: OpenCode failed to load the agent-guard plugin: {err}"))
                continue
        if a.name not in seen:
            add(("warn", f"{a.title}: no hook events from it in 30 days. Start a session to check its hooks run"
                         + (f" ({a.notes})" if a.notes else "")))

    for name, spec in KNOWN_AGENTS.items():
        bins = [b for b in spec["bin"] if b not in covered_bins and shutil.which(b)]
        dirs = [d for d in spec["dirs"] if str(expand(d)) not in covered_dirs and expand(d).exists()]
        if bins or dirs:
            found = ", ".join(bins + [contract(expand(d)) for d in dirs])
            add(("warn", f"unwired agent: {name} ({found}) — add adapters/{name}.yaml, then `guard install`"))
    return out


def _days_ago(n: int) -> float:
    import time

    return time.time() - n * 86400


def quick_issues() -> list[str]:
    """Problems worth a notification (pending proposals and quiet agents only show in `guard doctor`)."""
    return [m for lvl, m in results() if lvl in ("fail", "warn") and "proposal" not in m and "no hook events" not in m]


def render(res: list[tuple[str, str]]) -> str:
    icon = {"ok": "✓", "warn": "!", "fail": "✗", "info": "·"}
    return "\n".join(f"{icon[lvl]} {msg}" for lvl, msg in res)
