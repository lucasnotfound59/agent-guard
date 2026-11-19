"""Wording of warnings: one voice for agents, one for the user, one for terminals."""

from __future__ import annotations

from pathlib import Path

from agent_guard.checks import Finding
from agent_guard.rules import Rule

AGENT_FOOTER = (
    "Fix these now, or tell the user why they should stay. To keep a line on purpose, add "
    "`agent-guard: allow <rule-id> <reason>` on it. Never bypass hooks (--no-verify) or edit agent-guard rules."
)


def for_agent(findings: list[Finding], context: str = "your last change") -> str:
    if not findings:
        return ""
    lines = [f"agent-guard (warn-only) found {len(findings)} issue(s) in {context}:"]
    for f in findings:
        head = f"- [{f.rule.severity}] {f.rule.id}"
        if f.where:
            head += f" at {f.where}"
        snip = f.snippet()
        if snip:
            head += f": `{snip}`"
        lines.append(head)
        lines.append(f"  why: {f.rule.why}  fix: {f.rule.fix}")
    lines.append(AGENT_FOOTER)
    return "\n".join(lines)


def for_user(findings: list[Finding], where: str = "") -> str:
    if not findings:
        return ""
    ids = sorted({f.rule.id for f in findings})
    hard = sum(1 for f in findings if f.rule.severity == "hard")
    s = f"agent-guard: {len(findings)} warning(s)"
    if hard:
        s += f" ({hard} hard)"
    s += ": " + ", ".join(ids[:5]) + (" …" if len(ids) > 5 else "")
    if where:
        s += f" in {where}"
    return s


def for_terminal(findings: list[Finding], title: str) -> str:
    if not findings:
        return ""
    out = [f"\n⚠ agent-guard (warn-only) — {title}: {len(findings)} warning(s)"]
    for f in findings:
        loc = f" {f.where}" if f.where else ""
        out.append(f"  [{f.rule.severity}] {f.rule.id}{loc}")
        if f.snippet():
            out.append(f"      {f.snippet()}")
        out.append(f"      fix: {f.rule.fix}")
    out.append("  Nothing was blocked. Fix it, or keep a line with `agent-guard: allow <rule-id> <reason>`.\n")
    return "\n".join(out)


def rules_summary(rules: list[Rule], root: Path | None, disabled: dict[str, str] | None = None) -> str:
    """What an agent sees at session start: the resolve step."""
    where = root.name if root else "this folder (not a git repo)"
    hard = [r for r in rules if r.severity == "hard"]
    soft = [r for r in rules if r.severity == "soft"]
    lines = [f"agent-guard is active on this machine (warn-only). Rules for {where}:"]
    if hard:
        lines.append("Hard: " + "; ".join(f"{r.id} ({r.title})" for r in hard))
    if soft:
        lines.append("Soft: " + "; ".join(f"{r.id} ({r.title})" for r in soft))
    if disabled:
        lines.append("Disabled here: " + "; ".join(f"{k} ({v})" for k, v in disabled.items()))
    lines.append(
        "You get warnings after edits, commands and commits: fix them or tell the user why not. "
        "Commit messages use Conventional Commits (feat:, fix:, patch:, docs:, refactor:, test:, chore:, rebase: ...). "
        "When the user corrects a general habit of yours, propose a rule with `guard propose --help`; "
        "only the user can approve it. Full rules: `guard rules`."
    )
    return "\n".join(lines)
