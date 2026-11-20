"""Machine-wide views: which rules each agent breaks most (report), and which bad
patterns already exist in a repo for agents to copy (scan)."""

from __future__ import annotations

import json
import re
import time
from collections import Counter
from pathlib import Path

from agent_guard import events, secrets
from agent_guard.checks import check_files
from agent_guard.diff import _git, whole_file
from agent_guard.rules import active_rules, path_matches


def parse_since(s: str) -> float | None:
    """Start of the window as a Unix time; None for "all"."""
    if s.strip() == "all":
        return None
    m = re.fullmatch(r"(\d+)([hdw])", s.strip())
    if not m:
        raise ValueError("use e.g. 24h, 7d, 4w, or all")
    n, unit = int(m.group(1)), m.group(2)
    return time.time() - n * {"h": 3600, "d": 86400, "w": 604800}[unit]


ERROR_EVENTS = ("hook_error", "git_hook_error", "doctor_error")


def repo_label(repo: str | None) -> str:
    return Path(repo).name if repo else "(no repo)"


def summary(recs: list[dict]) -> dict:
    """Counts behind `guard report` and the dashboard overview, from log records already in the window."""
    finds = [r for r in recs if r.get("event") == "finding"]

    def ranked(counter: Counter, n: int | None = None) -> list[dict]:
        return [{"key": k, "count": v} for k, v in counter.most_common(n)]

    severity = {r.get("rule"): r.get("severity") for r in finds}
    by_rule = ranked(Counter(r.get("rule") for r in finds))
    for row in by_rule:
        row["severity"] = severity.get(row["key"])
    by_repo = ranked(Counter(r.get("repo") or "" for r in finds))
    for row in by_repo:
        row["label"] = repo_label(row["key"] or None)
    errors = [r for r in recs if r.get("event") in ERROR_EVENTS]
    return {
        "warnings": len(finds),
        "hard": sum(1 for r in finds if r.get("severity") == "hard"),
        "soft": sum(1 for r in finds if r.get("severity") == "soft"),
        "kept": sum(1 for r in recs if r.get("event") == "suppressed"),
        "sessions": sum(1 for r in recs if r.get("event") == "session_start"),
        "unresolved_at_stop": sum(1 for r in recs if r.get("event") == "stop_summary"),
        "proposals": sum(1 for r in recs if r.get("event") == "proposal"),
        "project_check_failures": sum(1 for r in recs if r.get("event") == "project_check"),
        "errors": len(errors),
        "last_error": errors[-1].get("error") if errors else None,
        "last_error_event": errors[-1].get("event") if errors else None,
        "by_rule": by_rule,
        "by_agent": ranked(Counter(r.get("agent") or "?" for r in finds)),
        "by_repo": by_repo,
        "by_kind": ranked(Counter(r.get("kind") or "?" for r in finds)),
        "habits": [{"agent": a, "rule": rule, "count": n}
                   for (a, rule), n in Counter((r.get("agent") or "?", r.get("rule")) for r in finds).most_common(12)],
    }


def report(since: str = "7d", as_json: bool = False) -> str:
    s = summary(events.read_log(parse_since(since)))
    if as_json:
        return json.dumps({"since": since, **s}, indent=2, ensure_ascii=False)
    window = "all time" if since.strip() == "all" else f"last {since}"
    out = [f"agent-guard report — {window}: {s['warnings']} warning(s) across every agent and repo"]
    if not s["warnings"]:
        out.append("Nothing flagged. (Sessions started: %d)" % s["sessions"])
        return "\n".join(out)

    def table(title: str, rows: list[tuple[str, int]], n: int = 10) -> None:
        out.append(f"\n{title}")
        for k, v in rows[:n]:
            out.append(f"  {v:5d}  {k}")

    table("By rule", [(r["key"], r["count"]) for r in s["by_rule"]])
    table("By agent", [(r["key"], r["count"]) for r in s["by_agent"]])
    by_name: Counter = Counter()
    for r in s["by_repo"]:
        by_name[r["label"]] += r["count"]
    table("By repo", by_name.most_common())
    table("Agent × rule (habits)", [(f"{h['agent']} · {h['rule']}", h["count"]) for h in s["habits"]], 12)
    if s["kept"]:
        out.append(f"\n{s['kept']} line(s) kept on purpose with `agent-guard: allow` (reasons in the log)")
    if s["proposals"]:
        out.append(f"{s['proposals']} rule proposal(s) from agents; run `guard review`")
    if s["errors"]:
        out.append(f"! {s['errors']} hook error(s); last: {s['last_error']}")
    return "\n".join(out)


def scan(path: str, rule_id: str | None = None, limit: int = 5) -> str:
    rules, project = active_rules(path)
    root = project.root
    if not root:
        return f"{path} is not inside a git repository"
    rules = [r for r in rules if r.kind in ("added-line-forbidden", "path-forbidden") and r.check.get("scan", True)
             and (rule_id is None or r.id == rule_id)]
    tracked = [p for p in _git(["ls-files", "-z"], root).stdout.split("\0") if p]
    files = [whole_file(root, p, status="M") for p in tracked]
    findings = check_files(rules, files, project.exclude_paths, include_size=False)
    secret_rule = next((r for r in rules if r.id == "no-secrets"), None)
    if secret_rule and secrets.available():
        known = set(tracked)  # gitleaks also reads untracked and ignored files; scan covers tracked ones
        extra = [f for f in secrets.directory(root, secret_rule)
                 if f.path in known and not path_matches(f.path, project.exclude_paths)]
        findings = secrets.merge(findings, extra)
    by_rule: dict[str, list] = {}
    for f in findings:
        by_rule.setdefault(f.rule.id, []).append(f)
    out = [f"agent-guard scan of {root.name}: existing code that agents may copy ({len(tracked)} tracked files)"]
    if not by_rule:
        out.append("  clean")
    for rid, fs in sorted(by_rule.items(), key=lambda kv: -len(kv[1])):
        top = Counter(f.path for f in fs).most_common(limit)
        out.append(f"  {len(fs):5d}  {rid}  ({fs[0].rule.severity})")
        for p, n in top:
            out.append(f"         {n:4d}  {p}")
    out.append("New lines are checked on every edit and commit; these existing ones are only reported here.")
    return "\n".join(out)
