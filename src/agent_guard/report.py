"""Machine-wide views: which rules each agent breaks most (report), and which bad
patterns already exist in a repo for agents to copy (scan)."""

from __future__ import annotations

import re
import time
from collections import Counter
from pathlib import Path

from agent_guard import events, secrets
from agent_guard.checks import check_files
from agent_guard.diff import _git, whole_file
from agent_guard.rules import active_rules, path_matches


def parse_since(s: str) -> float:
    m = re.fullmatch(r"(\d+)([hdw])", s.strip())
    if not m:
        raise ValueError("use e.g. 24h, 7d, 4w")
    n, unit = int(m.group(1)), m.group(2)
    return time.time() - n * {"h": 3600, "d": 86400, "w": 604800}[unit]


def report(since: str = "7d") -> str:
    recs = events.read_log(parse_since(since))
    finds = [r for r in recs if r.get("event") == "finding"]
    out = [f"agent-guard report — last {since}: {len(finds)} warning(s) across every agent and repo"]
    if not finds:
        out.append("Nothing flagged. (Sessions started: %d)" % sum(1 for r in recs if r.get("event") == "session_start"))
        return "\n".join(out)

    def table(title: str, counter: Counter, n: int = 10) -> None:
        out.append(f"\n{title}")
        for k, v in counter.most_common(n):
            out.append(f"  {v:5d}  {k}")

    table("By rule", Counter(r.get("rule") for r in finds))
    table("By agent", Counter(r.get("agent") for r in finds))
    table("By repo", Counter(Path(r["repo"]).name if r.get("repo") else "(no repo)" for r in finds))
    table("Agent × rule (habits)", Counter(f"{r.get('agent')} · {r.get('rule')}" for r in finds), 12)
    sup = [r for r in recs if r.get("event") == "suppressed"]
    if sup:
        out.append(f"\n{len(sup)} line(s) kept on purpose with `agent-guard: allow` (reasons in the log)")
    props = [r for r in recs if r.get("event") == "proposal"]
    if props:
        out.append(f"{len(props)} rule proposal(s) from agents; run `guard review`")
    errs = [r for r in recs if r.get("event") == "hook_error"]
    if errs:
        out.append(f"! {len(errs)} hook error(s); last: {errs[-1].get('error')}")
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
