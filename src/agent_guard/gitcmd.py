"""Git hook handlers (called by git-hooks/dispatcher for every repo on the machine).

Warn-only: we print to stderr (agents see it in their command output, people
see it in the terminal), log, and notify. We always exit 0; the dispatcher then
runs the repo's own hook and returns *its* exit code.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

from agent_guard import config, events, messages, secrets
from agent_guard.checks import active, check_commit_message, check_files
from agent_guard.diff import staged
from agent_guard.rules import ProjectConfig, active_rules, path_matches

FTCKB_SCRIPT = "tools/FTC-Knowledge-Bank/.agents/skills/ftckb-integrate/scripts/project.py"
FTCKB_CLI = "tools/FTC-Knowledge-Bank/apps/knowledge-cli/build/install/ftckb/bin/ftckb"

AGENT_ENV_HINTS = [
    ("CLAUDECODE", "claude"),
    ("CLAUDE_CODE_ENTRYPOINT", "claude"),
    ("CODEX_THREAD_ID", "codex"),
    ("CODEX_SANDBOX", "codex"),
    ("CODEX_HOME", "codex"),
    ("GEMINI_CLI", "gemini"),
    ("CURSOR_AGENT", "cursor"),
    ("CURSOR_TRACE_ID", "cursor"),
    ("OPENCODE", "opencode"),
    ("OPENCODE_SESSION", "opencode"),
    ("DSH_HOME", "dsh"),
    ("COPILOT_CLI", "copilot"),
]


def detect_agent() -> str:
    for var, name in AGENT_ENV_HINTS:
        if os.environ.get(var):
            return name
    return "human-or-unknown"


def _say(text: str) -> None:
    if text:
        print(text, file=sys.stderr)


def pre_commit(cwd: str | None = None) -> int:
    cwd = cwd or os.getcwd()
    rules, project = active_rules(cwd)
    root = project.root
    if not root:
        return 0
    agent = detect_agent()
    findings = check_files(rules, staged(root), project.exclude_paths)
    secret_rule = next((r for r in rules if r.id == "no-secrets"), None)
    if secret_rule and secrets.available():
        extra = [f for f in secrets.staged(root, secret_rule) if not path_matches(f.path or "", project.exclude_paths)]
        findings = secrets.merge(findings, extra)
    for f in findings:
        events.log("suppressed" if f.suppressed else "finding", agent=agent, kind="commit", repo=str(root), **f.to_event())
    live = active(findings)
    _say(messages.for_terminal(live, "staged changes"))
    problems = project_checks(root, project)
    for p in problems:
        events.log("project_check", agent=agent, repo=str(root), detail=p[:300])
    if problems:
        _say("\n⚠ agent-guard project checks (warn-only):\n" + "\n".join(problems) + "\n")
    if project.problems:
        _say("agent-guard: problems in .agent-guard.yaml: " + "; ".join(project.problems))
    if live or problems:
        summary = messages.for_user(live) or "agent-guard: project checks reported problems"
        events.notify(f"agent-guard · commit · {root.name}", summary, key=f"commit:{root}")
    _maybe_doctor()
    return 0


def commit_msg(msg_file: str, cwd: str | None = None) -> int:
    cwd = cwd or os.getcwd()
    rules, project = active_rules(cwd)
    try:
        message = Path(msg_file).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return 0
    findings = active(check_commit_message(rules, message))
    agent = detect_agent()
    for f in findings:
        events.log("finding", agent=agent, kind="commit-message", repo=str(project.root) if project.root else None,
                   **f.to_event())
    if findings:
        _say(messages.for_terminal(findings, "commit message",
                                   "Nothing was blocked. Reword it with `git commit --amend` if it isn't pushed yet."))
        name = project.root.name if project.root else "repo"
        events.notify(f"agent-guard · commit · {name}", messages.for_user(findings), key=f"msg:{project.root}")
    return 0


def project_checks(root: Path, project: ProjectConfig) -> list[str]:
    """Run the repo's own checks (from .agent-guard.yaml, plus FTC-KB when integrated)."""
    checks = list(project.checks)
    if (root / ".ftckb" / "project.yaml").exists() and (root / FTCKB_SCRIPT).exists():
        if (root / FTCKB_CLI).exists():
            checks.append({"name": "FTC Knowledge Bank check", "run": f"python3 {FTCKB_SCRIPT} check --project ."})
        else:
            _say("agent-guard: FTC Knowledge Bank is integrated here but its CLI is not built; "
                 "skipping `project.py check` (build it to get FTC rule checks at commit time).")
    timeout = int(config.load()["project_check_timeout"])
    problems: list[str] = []
    for c in checks:
        name = str(c.get("name") or c["run"])
        try:
            r = subprocess.run(str(c["run"]), shell=True, cwd=str(root), capture_output=True, text=True,
                               timeout=int(c.get("timeout", timeout)), stdin=subprocess.DEVNULL)
        except subprocess.TimeoutExpired:
            problems.append(f"  {name}: timed out, skipped")
            continue
        if r.returncode != 0:
            tail = "\n".join((r.stdout + r.stderr).strip().splitlines()[-12:])
            problems.append(f"  {name}: exit {r.returncode}\n" + "\n".join("      " + t for t in tail.splitlines()))
    return problems


def _maybe_doctor() -> None:
    from agent_guard.paths import state_home

    stamp = state_home() / "state" / "last-doctor"
    hours = float(config.load()["doctor_interval_hours"])
    try:
        if stamp.exists() and time.time() - stamp.stat().st_mtime < hours * 3600:
            return
        stamp.parent.mkdir(parents=True, exist_ok=True)
        stamp.touch()
        from agent_guard import doctor

        issues = doctor.quick_issues()
        if issues:
            events.log("doctor", issues=issues)
            events.notify("agent-guard · doctor", f"{len(issues)} issue(s): {issues[0]} — run `guard doctor`",
                          key="doctor", force=True)
    except Exception as e:
        events.log("doctor_error", error=f"{type(e).__name__}: {e}")
