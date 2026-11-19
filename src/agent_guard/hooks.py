"""What happens on each agent hook event. Warn-only: we add context and messages,
never deny. Any crash returns the format's neutral output, so a bug in
agent-guard can never stop an agent from working."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from agent_guard import config, events, hookio, messages
from agent_guard.checks import Finding, active, check_command, check_files
from agent_guard.diff import FileDiff, changed_paths, from_text, working
from agent_guard.hookio import HookInput, HookOutput
from agent_guard.rules import ProjectConfig, Rule, active_rules, find_repo_root

# Formats that also get a pre-command hook, so the user already saw command warnings.
HAS_PRE_COMMAND = {"claude", "codex", "gemini"}


def run(agent: str, fmt: str, event_hint: str, stdin_text: str) -> str:
    mod = hookio.get(fmt)
    try:
        payload = json.loads(stdin_text) if stdin_text and stdin_text.strip() else {}
        if not isinstance(payload, dict):
            payload = {}
        inp = mod.parse(payload, event_hint)
        out = handle(agent, fmt, inp)
        return mod.render(inp, out)
    except Exception as e:  # never break the agent
        events.log("hook_error", agent=agent, format=fmt, hint=event_hint, error=f"{type(e).__name__}: {e}")
        return mod.neutral(event_hint)


class _Ctx:
    def __init__(self, agent: str, fmt: str, inp: HookInput):
        self.agent, self.fmt, self.inp = agent, fmt, inp
        self.cwd = inp.cwd or os.getcwd()
        self.state = events.SessionState(agent, inp.session_id or f"pid{os.getppid()}")
        self._rules: dict[str, tuple[list[Rule], ProjectConfig]] = {}

    def rules_for(self, path: Path | str | None) -> tuple[list[Rule], ProjectConfig]:
        key = str(path or self.cwd)
        if key not in self._rules:
            self._rules[key] = active_rules(path or self.cwd)
        return self._rules[key]

    def record(self, findings: list[Finding], kind: str, root: Path | None) -> None:
        for f in findings:
            events.log("suppressed" if f.suppressed else "finding", agent=self.agent, kind=kind,
                       repo=str(root) if root else None, cwd=self.cwd, session=self.inp.session_id or None,
                       **f.to_event())

    def fresh(self, findings: list[Finding], channel: str = "a") -> list[Finding]:
        out = []
        for f in active(findings):
            k = f"{channel}:{f.key}"
            if self.state.is_new(k):
                self.state.mark(k)
                out.append(f)
        return out

    def notify(self, text: str, root: Path | None) -> None:
        if text:
            where = root.name if root else Path(self.cwd).name
            events.notify(f"agent-guard · {self.agent} · {where}", text, key=f"{self.agent}:{root or self.cwd}")


def handle(agent: str, fmt: str, inp: HookInput) -> HookOutput:
    if inp.event not in hookio.EVENTS or inp.event == "ignore":
        return HookOutput()
    ctx = _Ctx(agent, fmt, inp)
    try:
        return {
            "session_start": _session_start,
            "pre_command": _pre_command,
            "post_command": _post_command,
            "post_edit": _post_edit,
            "stop": _stop,
        }[inp.event](ctx)
    finally:
        ctx.state.data["last_ts"] = time.time()
        ctx.state.save()


def _session_start(ctx: _Ctx) -> HookOutput:
    rules, project = ctx.rules_for(ctx.cwd)
    events.log("session_start", agent=ctx.agent, cwd=ctx.cwd, session=ctx.inp.session_id or None,
               repo=str(project.root) if project.root else None, rules=len(rules))
    text = messages.rules_summary(rules, project.root, project.disabled)
    if project.problems:
        text += "\nagent-guard config problems in this repo: " + "; ".join(project.problems)
    return HookOutput(agent_text=text)


def _pre_command(ctx: _Ctx) -> HookOutput:
    rules, project = ctx.rules_for(ctx.cwd)
    findings = check_command(rules, ctx.inp.command or "")
    ctx.record(findings, "command", project.root)
    new = ctx.fresh(findings, channel="u")
    if not new:
        return HookOutput()
    text = "agent-guard ⚠ " + "; ".join(f"{f.rule.id}: {f.rule.why}" for f in new)
    ctx.notify(text, project.root)
    return HookOutput(user_text=text)


def _post_command(ctx: _Ctx) -> HookOutput:
    rules, project = ctx.rules_for(ctx.cwd)
    cmd_findings = check_command(rules, ctx.inp.command or "")
    if ctx.fmt not in HAS_PRE_COMMAND:
        ctx.record(cmd_findings, "command", project.root)
    new_cmd = ctx.fresh(cmd_findings, channel="a")
    file_findings: list[Finding] = []
    root = project.root
    if root:
        since = float(ctx.state.data.get("last_ts") or (time.time() - 300)) - 1
        paths = changed_paths(root)
        if len(paths) <= config.load()["post_command_max_files"]:
            recent = [p for p in paths if _mtime(root / p) >= since]
            for p in recent:
                ctx.state.touch(str(root), p)
            if recent:
                file_findings = check_files(rules, working(root, recent), project.exclude_paths, include_size=False)
                ctx.record(file_findings, "edit", root)
    new_files = ctx.fresh(file_findings)
    agent_text = messages.for_agent(new_cmd + new_files, "your last command")
    user_findings = new_files + ([] if ctx.fmt in HAS_PRE_COMMAND else new_cmd)
    user_text = messages.for_user(user_findings)
    ctx.notify(user_text, root)
    return HookOutput(agent_text=agent_text, user_text=user_text)


def _post_edit(ctx: _Ctx) -> HookOutput:
    by_root: dict[Path | None, list[tuple[str, str]]] = {}
    for f in ctx.inp.files:
        p = Path(f) if os.path.isabs(f) else Path(ctx.cwd) / f
        root = find_repo_root(p.parent if p.parent.exists() else ctx.cwd)
        by_root.setdefault(root, []).append((f, str(p)))
    all_new: list[Finding] = []
    roots: list[Path] = []
    for root, items in by_root.items():
        rules, project = ctx.rules_for(root or ctx.cwd)
        diffs: list[FileDiff] = []
        if root:
            rels = []
            for _, abs_p in items:
                try:
                    rels.append(str(Path(abs_p).resolve().relative_to(root.resolve())))
                except ValueError:
                    continue
            for r in rels:
                ctx.state.touch(str(root), r)
            diffs = working(root, rels)
            roots.append(root)
        else:
            diffs = [from_text(orig, ctx.inp.edits[orig]) for orig, _ in items if orig in ctx.inp.edits]
        findings = check_files(rules, diffs, project.exclude_paths, include_size=False)
        ctx.record(findings, "edit", root)
        all_new += ctx.fresh(findings)
    where = ", ".join(sorted({f.path for f in all_new if f.path}))[:120]
    user_text = messages.for_user(all_new, where)
    ctx.notify(user_text, roots[0] if roots else None)
    return HookOutput(agent_text=messages.for_agent(all_new), user_text=user_text)


def _stop(ctx: _Ctx) -> HookOutput:
    outstanding: list[Finding] = []
    for root_s, rels in ctx.state.data.get("touched", {}).items():
        root = Path(root_s)
        if not root.exists() or not rels:
            continue
        rules, project = ctx.rules_for(root)
        outstanding += active(check_files(rules, working(root, rels), project.exclude_paths, include_size=False))
    keys = sorted(f.key for f in outstanding)
    if not outstanding or keys == ctx.state.data.get("last_stop"):
        return HookOutput()
    ctx.state.data["last_stop"] = keys
    text = messages.for_user(outstanding, "files this session touched") + " (still unresolved)"
    events.log("stop_summary", agent=ctx.agent, cwd=ctx.cwd, session=ctx.inp.session_id or None,
               outstanding=len(outstanding), rules=sorted({f.rule.id for f in outstanding}))
    ctx.notify(text, None)
    return HookOutput(user_text=text, agent_text=messages.for_agent(outstanding, "files you changed this session"))


def _mtime(p: Path) -> float:
    try:
        return p.stat().st_mtime
    except OSError:
        return time.time()  # deleted files count as just changed
