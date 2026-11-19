"""The learning loop: agents propose rules, only the user approves them.

`guard propose` writes rules/candidates/<id>.yaml (never enforced).
`guard review` / `guard approve` need an interactive terminal, which agents
don't have, so an agent cannot approve its own proposal.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import yaml

from agent_guard import config, events
from agent_guard.checks import check_files
from agent_guard.diff import _git, whole_file
from agent_guard.paths import expand, repo_root
from agent_guard.rules import KINDS, Rule, global_rules, validate

CAND = "candidates"


def _dir(sub: str) -> Path:
    return repo_root() / "rules" / sub


def _dump(rule: Rule) -> str:
    return yaml.safe_dump(rule.to_dict(), sort_keys=False, allow_unicode=True, width=100)


def propose(*, id: str, title: str, kind: str, patterns: list[str], severity: str, why: str, fix: str,
            evidence: str, agent: str, applies_to: list[str] | None = None, exclude: list[str] | None = None,
            paths: list[str] | None = None) -> tuple[bool, str]:
    existing = {r.id for s in ("active", "candidate", "retired") for r in global_rules(s)}
    if id in existing:
        return False, f"a rule with id {id!r} already exists; pick another id or ask the user to edit it"
    check: dict = {"kind": kind}
    if kind == "path-forbidden":
        check["paths"] = paths or patterns
    elif len(patterns) == 1:
        check["pattern"] = patterns[0]
    else:
        check["patterns"] = patterns
    rule = Rule(id=id, title=title, severity=severity, status="candidate", check=check, why=why, fix=fix,
                applies_to=applies_to or ["**"], exclude=exclude or [],
                source={"proposed_by": agent, "proposed_at": time.strftime("%Y-%m-%d"), "evidence": evidence})
    errs = validate(rule)
    if errs:
        return False, "invalid rule: " + "; ".join(errs) + f"\nkinds: {', '.join(sorted(KINDS))}"
    path = _dir(CAND) / f"{id}.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_dump(rule), encoding="utf-8")
    events.log("proposal", agent=agent, rule=id, evidence=evidence[:300])
    events.notify("agent-guard · new rule proposal", f"{agent} proposed {id}: {title}. Run `guard review`.",
                  key=f"proposal:{id}", force=True)
    return True, (f"Proposed {id} (saved to rules/candidates). It is NOT active yet: the user must approve it "
                  f"with `guard review` in a terminal. Tell the user you proposed it.")


def _repos(limit: int = 60) -> list[Path]:
    found: list[Path] = []
    for root in config.load()["scan_roots"]:
        base = expand(root)
        if not base.is_dir():
            continue
        r = subprocess.run(["find", str(base), "-maxdepth", "4", "-name", ".git", "-type", "d",
                            "-not", "-path", "*/node_modules/*", "-not", "-path", "*/.worktrees/*"],
                           capture_output=True, text=True, timeout=30)
        found += [Path(p).parent for p in r.stdout.split()]
    return found[:limit]


def dry_run(rule: Rule, time_budget: float = 12.0) -> list[tuple[str, int]]:
    """How many existing lines in your repos the rule would have flagged (it only checks new lines)."""
    start, hits = time.time(), []
    for repo in _repos():
        if time.time() - start > time_budget:
            break
        files = [p for p in _git(["ls-files", "-z"], repo).stdout.split("\0") if p and rule.applies(p)][:3000]
        n = len(check_files([rule], [whole_file(repo, f) for f in files], include_size=False))
        if n:
            hits.append((repo.name, n))
    return sorted(hits, key=lambda x: -x[1])


def require_tty() -> str | None:
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return ("Approving rules needs the user at an interactive terminal. Agents cannot approve rules; "
                "ask the user to run `guard review`.")
    return None


def approve(rule_id: str, note: str = "") -> str:
    src = _dir(CAND) / f"{rule_id}.yaml"
    if not src.exists():
        return f"no candidate {rule_id!r}"
    data = yaml.safe_load(src.read_text(encoding="utf-8"))
    data["status"] = "active"
    data.setdefault("source", {})["approved_at"] = time.strftime("%Y-%m-%d")
    data["source"]["approved_by"] = "user"
    if note:
        data["source"]["approval_note"] = note
    dst = _dir("active") / f"{rule_id}.yaml"
    dst.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=100), encoding="utf-8")
    src.unlink()
    events.log("approved", rule=rule_id)
    _commit([dst, src], f"feat(rules): add {rule_id}")
    return f"approved {rule_id}: now active for every agent and repo"


def reject(rule_id: str, reason: str) -> str:
    src = _dir(CAND) / f"{rule_id}.yaml"
    if not src.exists():
        return f"no candidate {rule_id!r}"
    data = yaml.safe_load(src.read_text(encoding="utf-8"))
    data["status"] = "rejected"
    data.setdefault("source", {})["rejected_reason"] = reason
    dst = _dir("retired") / f"{rule_id}.yaml"
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=100), encoding="utf-8")
    src.unlink()
    events.log("rejected", rule=rule_id, reason=reason)
    _commit([dst, src], f"chore(rules): reject {rule_id}")
    return f"rejected {rule_id} (kept in rules/retired for history)"


def _commit(paths: list[Path], message: str) -> None:
    root = repo_root()
    rel = [str(p.relative_to(root)) for p in paths]
    subprocess.run(["git", "add", "-A", "--", *rel], cwd=root, capture_output=True)
    # The user's own approval commit: skip our pre-commit hook so it doesn't flag no-rule-tampering.
    subprocess.run(["git", "-c", "core.hooksPath=/dev/null", "commit", "-q", "-m", message, "--", *rel],
                   cwd=root, capture_output=True)


def interactive() -> int:
    msg = require_tty()
    if msg:
        print(msg)
        return 2
    cands = global_rules("candidate")
    if not cands:
        print("No rule proposals waiting.")
        return 0
    for rule in cands:
        print("\n" + "=" * 72)
        print(_dump(rule))
        print("Checking your repos for existing matches…")
        hits = dry_run(rule)
        print("  would match existing code in: " + (", ".join(f"{n} ({c})" for n, c in hits[:8]) or "none")
              + "\n  (rules only warn on NEW lines, so existing code is never flagged)")
        while True:
            ans = input(f"[a]pprove / [r]eject / [e]dit / [s]kip {rule.id}? ").strip().lower()
            if ans in ("a", "approve"):
                print(approve(rule.id))
                break
            if ans in ("r", "reject"):
                print(reject(rule.id, input("reason: ").strip() or "rejected by user"))
                break
            if ans in ("e", "edit"):
                editor = __import__("os").environ.get("EDITOR", "nano")
                subprocess.run([editor, str(rule.file)])
                fresh = Rule.from_dict(yaml.safe_load(Path(rule.file).read_text(encoding="utf-8")), rule.file)
                errs = validate(fresh)
                print("invalid: " + "; ".join(errs) if errs else "saved. approve or reject it now.")
                continue
            if ans in ("s", "skip", ""):
                break
    return 0
