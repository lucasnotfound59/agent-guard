"""guard — one rulebook for every AI coding agent on this machine.

Agents: read `guard rules`, fix what warnings tell you, propose new rules with
`guard propose`. The user approves with `guard review`.
"""

from __future__ import annotations

import argparse
import json
import sys

from agent_guard import __version__


def _cmd_rules(a: argparse.Namespace) -> int:
    from agent_guard import messages
    from agent_guard.rules import active_rules, global_rules

    if a.candidates:
        rules = global_rules("candidate")
        print("\n".join(f"{r.id} [{r.severity}] {r.title} (by {r.source.get('proposed_by', '?')})" for r in rules)
              or "No proposals waiting.")
        return 0
    rules, project = active_rules(a.path)
    if a.json:
        print(json.dumps([r.to_dict() | {"origin": r.origin} for r in rules], indent=2, ensure_ascii=False))
        return 0
    if a.brief:
        print(messages.rules_summary(rules, project.root, project.disabled))
        return 0
    for r in rules:
        print(f"[{r.severity}] {r.id} — {r.title}{'  (project rule)' if r.origin == 'project' else ''}")
        print(f"    why: {r.why}\n    fix: {r.fix}")
    for rid, reason in project.disabled.items():
        print(f"[off ] {rid} — disabled here: {reason}")
    for p in project.problems:
        print(f"! {p}")
    return 0


def _cmd_check(a: argparse.Namespace) -> int:
    from pathlib import Path

    from agent_guard import messages
    from agent_guard.checks import active, check_command, check_commit_message, check_files
    from agent_guard.diff import parse_unified, staged, working
    from agent_guard.rules import active_rules

    rules, project = active_rules(a.path)
    if a.command:
        findings = check_command(rules, a.command)
    elif a.message:
        findings = check_commit_message(rules, a.message)
    else:
        root = project.root
        if a.diff:
            files = parse_unified(Path(a.diff).read_text(encoding="utf-8", errors="replace"))
        elif not root:
            print("not inside a git repository (use --diff FILE)", file=sys.stderr)
            return 64
        elif a.staged:
            files = staged(root)
        else:
            files = working(root)
        findings = check_files(rules, files, project.exclude_paths)
    live = active(findings)
    if a.json:
        print(json.dumps([f.to_event() for f in findings], indent=2, ensure_ascii=False))
    else:
        print(messages.for_terminal(live, "check") or "agent-guard: no warnings")
    return 1 if (a.strict and live) else 0


def _cmd_hook(a: argparse.Namespace) -> int:
    from agent_guard import hooks

    out = hooks.run(a.agent, a.format, a.event, sys.stdin.read())
    if out:
        sys.stdout.write(out)
    return 0


def _cmd_git(a: argparse.Namespace) -> int:
    from agent_guard import gitcmd

    try:
        if a.hook == "pre-commit":
            return gitcmd.pre_commit()
        if a.hook == "commit-msg" and a.args:
            return gitcmd.commit_msg(a.args[0])
    except Exception as e:  # a guard bug must never stop a commit
        from agent_guard import events

        events.log("git_hook_error", hook=a.hook, error=f"{type(e).__name__}: {e}")
        print(f"agent-guard: internal error in {a.hook} hook ({e}); commit not affected", file=sys.stderr)
    return 0


def _cmd_propose(a: argparse.Namespace) -> int:
    from agent_guard import review

    ok, msg = review.propose(id=a.id, title=a.title, kind=a.kind, patterns=a.pattern or [], severity=a.severity,
                             why=a.why, fix=a.fix, evidence=a.evidence, agent=a.agent, applies_to=a.applies_to,
                             exclude=a.exclude, paths=a.paths)
    print(msg)
    return 0 if ok else 1


def _cmd_review(a: argparse.Namespace) -> int:
    from agent_guard import review

    return review.interactive()


def _cmd_approve(a: argparse.Namespace) -> int:
    from agent_guard import review

    msg = review.require_tty()
    if msg:
        print(msg)
        return 2
    if input(f"Type the rule id to approve it for every agent and repo ({a.id}): ").strip() != a.id:
        print("not approved")
        return 1
    print(review.approve(a.id, a.note or ""))
    return 0


def _cmd_reject(a: argparse.Namespace) -> int:
    from agent_guard import review

    msg = review.require_tty()
    if msg:
        print(msg)
        return 2
    print(review.reject(a.id, a.reason))
    return 0


def _cmd_report(a: argparse.Namespace) -> int:
    from agent_guard import report

    print(report.report(a.since))
    return 0


def _cmd_scan(a: argparse.Namespace) -> int:
    from agent_guard import report

    print(report.scan(a.path, a.rule))
    return 0


def _cmd_doctor(a: argparse.Namespace) -> int:
    from agent_guard import doctor

    res = doctor.results()
    if a.json:
        print(json.dumps([{"level": lvl, "message": m} for lvl, m in res], indent=2))
    else:
        print(doctor.render([r for r in res if not (a.quick and r[0] in ("ok", "info"))]))
    return 1 if any(lvl == "fail" for lvl, _ in res) else 0


def _cmd_install(a: argparse.Namespace) -> int:
    from agent_guard import install

    for line in install.install(dry_run=a.dry_run):
        print(("(dry run) " if a.dry_run else "") + line)
    if not a.dry_run:
        print("\nDone. Next: `guard doctor`. In Codex, open /hooks once and trust the agent-guard hooks.")
    return 0


def _cmd_uninstall(a: argparse.Namespace) -> int:
    from agent_guard import install

    for line in install.uninstall(purge=a.purge):
        print(line)
    return 0


def _cmd_skills(a: argparse.Namespace) -> int:
    from agent_guard import adapters, skills
    from agent_guard.state import InstallState

    for line in skills.sync(adapters.load_all(), InstallState(), dry_run=a.dry_run) or ["skills already in sync"]:
        print(line)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="guard", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--version", action="version", version=f"agent-guard {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("rules", help="show the rules that apply here (global + this repo)")
    s.add_argument("--path", default=".")
    s.add_argument("--json", action="store_true")
    s.add_argument("--brief", action="store_true", help="the short summary agents get at session start")
    s.add_argument("--candidates", action="store_true", help="list proposals waiting for approval")
    s.set_defaults(fn=_cmd_rules)

    s = sub.add_parser("check", help="check changes, a command, or a commit message")
    s.add_argument("--path", default=".")
    g = s.add_mutually_exclusive_group()
    g.add_argument("--staged", action="store_true", help="staged changes (what the commit hook checks)")
    g.add_argument("--diff", metavar="FILE", help="a unified diff file")
    g.add_argument("--command", metavar="CMD", help="a shell command")
    g.add_argument("--message", metavar="MSG", help="a commit message")
    s.add_argument("--json", action="store_true")
    s.add_argument("--strict", action="store_true", help="exit 1 when there are warnings")
    s.set_defaults(fn=_cmd_check)

    s = sub.add_parser("hook", help="(called by agent hooks) read the agent's hook JSON on stdin")
    s.add_argument("--agent", required=True)
    s.add_argument("--format", required=True, choices=["claude", "codex", "gemini", "cursor", "copilot", "generic"])
    s.add_argument("--event", default="auto")
    s.set_defaults(fn=_cmd_hook)

    s = sub.add_parser("git", help="(called by the global git hooks)")
    s.add_argument("hook")
    s.add_argument("args", nargs="*")
    s.set_defaults(fn=_cmd_git)

    s = sub.add_parser("propose", help="propose a new rule (agents: do this when the user corrects a habit)")
    s.add_argument("--id", required=True, help="kebab-case, e.g. no-print-debugging")
    s.add_argument("--title", required=True)
    s.add_argument("--kind", default="added-line-forbidden",
                   choices=["added-line-forbidden", "added-line-required", "path-forbidden", "command-forbidden",
                            "commit-message", "diff-size"])
    s.add_argument("--pattern", action="append", help="regex (repeatable)")
    s.add_argument("--paths", nargs="+", help="globs for path-forbidden")
    s.add_argument("--applies-to", nargs="+", help="globs, e.g. '*.ts' 'src/**'")
    s.add_argument("--exclude", nargs="+")
    s.add_argument("--severity", default="soft", choices=["soft", "hard"])
    s.add_argument("--why", required=True)
    s.add_argument("--fix", required=True)
    s.add_argument("--evidence", required=True, help="what the user said or what went wrong")
    s.add_argument("--agent", required=True, help="your name: claude, codex, gemini, cursor, opencode, dsh, ...")
    s.set_defaults(fn=_cmd_propose)

    s = sub.add_parser("review", help="(user) review rule proposals interactively")
    s.set_defaults(fn=_cmd_review)
    s = sub.add_parser("approve", help="(user) approve one proposal")
    s.add_argument("id")
    s.add_argument("--note")
    s.set_defaults(fn=_cmd_approve)
    s = sub.add_parser("reject", help="(user) reject one proposal")
    s.add_argument("id")
    s.add_argument("--reason", required=True)
    s.set_defaults(fn=_cmd_reject)

    s = sub.add_parser("report", help="which rules each agent breaks most, machine-wide")
    s.add_argument("--since", default="7d")
    s.set_defaults(fn=_cmd_report)

    s = sub.add_parser("scan", help="existing bad patterns in a repo that agents may copy")
    s.add_argument("path", nargs="?", default=".")
    s.add_argument("--rule")
    s.set_defaults(fn=_cmd_scan)

    s = sub.add_parser("doctor", help="verify every agent is wired; find unwired agents")
    s.add_argument("--quick", action="store_true")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=_cmd_doctor)

    s = sub.add_parser("install", help="wire every detected agent (idempotent)")
    s.add_argument("--dry-run", action="store_true")
    s.set_defaults(fn=_cmd_install)
    s = sub.add_parser("uninstall", help="remove agent-guard and restore originals")
    s.add_argument("--purge", action="store_true", help="also delete logs, backups and the venv")
    s.set_defaults(fn=_cmd_uninstall)

    s = sub.add_parser("skills", help="re-sync the shared skill pool")
    s.add_argument("--dry-run", action="store_true")
    s.set_defaults(fn=_cmd_skills)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.fn(args) or 0)
