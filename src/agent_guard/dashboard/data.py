"""What the dashboard shows. Every function here only reads: the event log, the rule files, git, and doctor.

The payloads are plain dicts so the HTTP layer only has to serialise them, and tests can call them directly.
"""

from __future__ import annotations

import hashlib
import threading
import time
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from agent_guard import adapters, doctor, events, report, review
from agent_guard.checks import Finding, check_command, check_commit_message, check_files, sensitive
from agent_guard.diff import parse_unified, staged, working
from agent_guard.paths import contract, log_file, repo_root
from agent_guard.rules import Rule, active_rules, global_rules, validate

WINDOWS: dict[str, float | None] = {"24h": 86400, "7d": 7 * 86400, "30d": 30 * 86400, "all": None}
CHECK_MODES = ("working", "staged", "diff", "command", "message")
MAX_PAGE = 200
HEALTH_TTL = 60.0


class BadRequest(ValueError):
    """The request names something that doesn't exist or isn't allowed; the message is shown to the user."""


# ------------------------------------------------------------------ inputs


def window_start(window: str) -> float | None:
    if window not in WINDOWS:
        raise BadRequest(f"window must be one of {', '.join(WINDOWS)}")
    span = WINDOWS[window]
    return None if span is None else time.time() - span


def repo_arg(value: str | None) -> Path | None:
    """A repo path from the page. It only selects which .agent-guard.yaml overlay applies."""
    if not value:
        return None
    p = Path(value).expanduser()
    if not p.is_absolute() or not p.is_dir():
        raise BadRequest(f"{value} is not an existing folder (use an absolute path)")
    return p


# ------------------------------------------------------------------ the log


class LogCache:
    """The parsed event log, reread only when a log file changes (the page polls every few seconds)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sig: tuple | None = None
        self._recs: list[tuple[float, dict[str, Any]]] = []

    def records(self) -> list[tuple[float, dict[str, Any]]]:
        """(unix time, record), oldest first. A record's index is its id: the log is append-only."""
        sig = events.log_signature()
        with self._lock:
            if sig != self._sig:
                recs = []
                for rec in events.read_log():
                    try:
                        t = datetime.fromisoformat(rec["ts"]).timestamp()
                    except (KeyError, TypeError, ValueError):
                        continue
                    recs.append((t, rec))
                self._recs, self._sig = recs, sig
            return self._recs

    def since(self, start: float | None) -> list[tuple[int, float, dict[str, Any]]]:
        return [(i, t, r) for i, (t, r) in enumerate(self.records()) if start is None or t >= start]


def version(log: LogCache) -> str:
    """Changes when anything the page shows changes: the log, any rule file, or config.yaml."""
    parts: list[Any] = [events.log_signature()]
    root = repo_root()
    for sub in ("active", "candidates", "retired"):
        for f in sorted((root / "rules" / sub).glob("*.yaml")):
            try:
                st = f.stat()
                parts.append((sub, f.name, st.st_size, st.st_mtime_ns))
            except OSError:
                continue
    try:
        parts.append(("config", (root / "config.yaml").stat().st_mtime_ns))
    except OSError:
        pass
    from agent_guard.dashboard import toolbox  # the Toolbox's files: agent configs, AGENTS.md, the skill pool

    for f in [*toolbox.watched_files()]:
        try:
            parts.append((str(f), f.stat().st_mtime_ns))
        except OSError:
            parts.append((str(f), None))
    return hashlib.sha1(repr(parts).encode()).hexdigest()[:16]


_SENSITIVE: dict[str, str] = {}


def _hidden_rules() -> dict[str, str]:
    """rule id -> reason, for rules whose matched lines must not be shown (see checks.sensitive)."""
    if not _SENSITIVE:
        for status in ("active", "candidate", "retired"):
            for r in global_rules(status):
                why = sensitive(r)
                if why:
                    _SENSITIVE[r.id] = why
        _SENSITIVE.setdefault("no-secrets", "it may hold a secret")
    return _SENSITIVE


def _event_item(i: int, rec: dict[str, Any]) -> dict[str, Any]:
    item = {"id": i, **rec, "repoName": report.repo_label(rec.get("repo")) if rec.get("repo") else None}
    why = _hidden_rules().get(str(rec.get("rule")))
    if why and item.get("snippet") and not str(item["snippet"]).startswith("[line hidden"):
        item["snippet"] = f"[line hidden: {why}]"  # logged before agent-guard stopped storing these lines
    return item


def known_repos(log: LogCache, limit: int = 60) -> list[dict[str, Any]]:
    """Repos agents have worked in (newest first) that still exist, plus this regulation repo."""
    last: dict[str, float] = {}
    for t, rec in log.records():
        if rec.get("repo"):
            last[rec["repo"]] = t
    own = str(repo_root())
    last.setdefault(own, 0.0)
    out = []
    for path, t in sorted(last.items(), key=lambda kv: -kv[1]):
        if Path(path).is_dir():
            out.append({"path": path, "label": _short(contract(path)), "name": Path(path).name, "last": t or None})
        if len(out) >= limit:
            break
    return out


def _short(label: str, width: int = 64) -> str:
    """Keep the start and the repo name of a long path: /private/tmp/claude-501/…/scratchpad/codex-live2."""
    if len(label) <= width:
        return label
    head, _, name = label.rpartition("/")
    keep = max(12, width - len(name) - 2)
    return f"{head[:keep]}…/{name}"


# ------------------------------------------------------------------ overview


def _buckets(window: str, finds: list[tuple[float, dict[str, Any]]]) -> dict[str, Any]:
    now = datetime.now().astimezone()
    if window == "24h":
        step, n = timedelta(hours=1), 24
        first = now.replace(minute=0, second=0, microsecond=0) - step * (n - 1)
    else:
        today = now.replace(hour=0, minute=0, second=0, microsecond=0)
        if window == "all":
            oldest = min((t for t, _ in finds), default=now.timestamp())
            days = max(1, (today - datetime.fromtimestamp(oldest).astimezone().replace(
                hour=0, minute=0, second=0, microsecond=0)).days + 1)
            step = timedelta(days=1) if days <= 60 else timedelta(weeks=1)
            n = days if days <= 60 else -(-days // 7)
        else:
            step, n = timedelta(days=1), 7 if window == "7d" else 30
        first = today - step * (n - 1)
    rows = [{"start": int((first + step * k).timestamp() * 1000), "hard": 0, "soft": 0} for k in range(n)]
    base, width = first.timestamp(), step.total_seconds()
    for t, rec in finds:
        k = int((t - base) // width)
        if 0 <= k < n:
            rows[k]["hard" if rec.get("severity") == "hard" else "soft"] += 1
    unit = "hour" if step == timedelta(hours=1) else "week" if step == timedelta(weeks=1) else "day"
    return {"unit": unit, "rows": rows}


def overview(log: LogCache, window: str) -> dict[str, Any]:
    recs = log.since(window_start(window))
    finds = [(t, r) for _, t, r in recs if r.get("event") == "finding"]
    counts = {"active": 0, "candidates": 0, "retired": 0, "invalid": 0}
    for status, key in (("active", "active"), ("candidate", "candidates"), ("retired", "retired")):
        for r in global_rules(status):
            counts[key] += 1
            if validate(r):
                counts["invalid"] += 1
    recent = [_event_item(i, r) for i, _, r in reversed(recs[-12:])]
    return {
        "window": window,
        "logPath": contract(log_file()),
        "logRecords": len(log.records()),
        "summary": report.summary([r for _, _, r in recs]),
        "series": _buckets(window, finds),
        "rules": counts,
        "recent": recent,
    }


# ------------------------------------------------------------------ rules


def _rule_item(r: Rule, stats: dict[str, Any] | None, here: str | None = None) -> dict[str, Any]:
    d = r.to_dict()
    file = r.file
    if file:
        try:
            file_label = str(file.relative_to(repo_root()))
        except ValueError:
            file_label = contract(file)
    else:
        file_label = None
    d.update(origin=r.origin, file=file_label, errors=validate(r), here=here,
             stats=stats or {"hits": 0, "kept": 0, "last": None, "agents": [], "repos": [], "recent": []})
    return d


def _rule_stats(recs: list[tuple[int, float, dict[str, Any]]]) -> dict[str, dict[str, Any]]:
    by: dict[str, dict[str, Any]] = {}
    for i, t, rec in recs:
        ev = rec.get("event")
        if ev not in ("finding", "suppressed") or not rec.get("rule"):
            continue
        s = by.setdefault(rec["rule"], {"hits": 0, "kept": 0, "last": None, "agents": Counter(),
                                        "repos": Counter(), "recent": []})
        if ev == "suppressed":
            s["kept"] += 1
            continue
        s["hits"] += 1
        s["last"] = t
        s["agents"][rec.get("agent") or "?"] += 1
        s["repos"][report.repo_label(rec.get("repo"))] += 1
        s["recent"].append(_event_item(i, rec))
    for s in by.values():
        s["agents"] = [{"key": k, "count": v} for k, v in s["agents"].most_common(5)]
        s["repos"] = [{"key": k, "count": v} for k, v in s["repos"].most_common(5)]
        s["recent"] = s["recent"][-5:][::-1]
    return by


def rules(log: LogCache, window: str, repo: str | None = None) -> dict[str, Any]:
    stats = _rule_stats(log.since(window_start(window)))
    root = repo_arg(repo)
    items: list[dict[str, Any]] = []
    overlay: dict[str, Any] | None = None
    here: dict[str, str] = {}
    project_rules: list[Rule] = []
    if root:
        applied, project = active_rules(root)
        here = {r.id: "on" for r in applied}
        here.update({rid: "disabled" for rid in project.disabled})
        project_rules = [r for r in applied if r.origin == "project"]
        overlay = {
            "root": str(project.root) if project.root else None,
            "label": contract(project.root) if project.root else contract(root),
            "isRepo": project.root is not None,
            "disabled": [{"id": k, "reason": v} for k, v in project.disabled.items()],
            "excludePaths": project.exclude_paths,
            "checks": [str(c.get("name") or c.get("run")) for c in project.checks],
            "problems": project.problems,
        }
    for status in ("active", "candidate", "retired"):
        for r in global_rules(status):
            items.append(_rule_item(r, stats.get(r.id), here.get(r.id, "off") if root and status == "active" else None))
    items += [_rule_item(r, stats.get(r.id), "on") for r in project_rules]
    return {"window": window, "rules": items, "overlay": overlay, "repos": known_repos(log)}


# ------------------------------------------------------------------ activity


FILTERS = ("event", "agent", "rule", "repo", "severity", "kind")


def activity(log: LogCache, window: str, filters: dict[str, str], offset: int = 0, limit: int = 50) -> dict[str, Any]:
    if offset < 0 or not 1 <= limit <= MAX_PAGE:
        raise BadRequest(f"offset must be >= 0 and limit between 1 and {MAX_PAGE}")
    recs = log.since(window_start(window))
    facets = {k: Counter(str(r.get(k)) for _, _, r in recs if r.get(k)).most_common(80) for k in FILTERS}
    query = (filters.get("q") or "").strip().lower()
    chosen = {k: filters[k] for k in FILTERS if filters.get(k)}

    def keep(rec: dict[str, Any]) -> bool:
        if any(str(rec.get(k)) != v for k, v in chosen.items()):
            return False
        return not query or any(query in str(v).lower() for v in rec.values())

    matched = [(i, r) for i, _, r in reversed(recs) if keep(r)]
    page = matched[offset : offset + limit]
    return {
        "window": window,
        "total": len(matched),
        "offset": offset,
        "limit": limit,
        "items": [_event_item(i, r) for i, r in page],
        "facets": {k: [{"key": key, "count": n, "label": report.repo_label(key) if k == "repo" else key}
                       for key, n in v] for k, v in facets.items()},
    }


# ------------------------------------------------------------------ proposals


def proposals(log: LogCache, window: str) -> dict[str, Any]:
    recs = log.since(window_start(window))
    evidence: dict[str, dict[str, Any]] = {}
    for i, _, rec in recs:
        if rec.get("event") == "proposal" and rec.get("rule"):
            evidence[rec["rule"]] = _event_item(i, rec)
    cands = []
    for r in global_rules("candidate"):
        item = _rule_item(r, None)
        item["logged"] = evidence.get(r.id)
        item["commands"] = {"review": "guard review", "approve": f"guard approve {r.id}",
                            "reject": f'guard reject {r.id} --reason "..."'}
        cands.append(item)
    decided = [_event_item(i, rec) for i, _, rec in reversed(recs) if rec.get("event") in ("approved", "rejected")]
    return {"window": window, "candidates": cands, "decided": decided[:30]}


def dry_run(rule_id: str) -> dict[str, Any]:
    """How many existing lines in your repos the rule would flag (rules only warn on new lines)."""
    rule = next((r for s in ("candidate", "active") for r in global_rules(s) if r.id == rule_id), None)
    if rule is None:
        raise BadRequest(f"no active or proposed rule {rule_id!r}")
    errs = validate(rule)
    if errs:
        raise BadRequest(f"{rule_id} is invalid, so it can't be dry-run: {'; '.join(errs)}")
    started = time.time()
    hits = review.dry_run(rule)
    return {"id": rule_id, "hits": [{"repo": name, "count": n} for name, n in hits],
            "seconds": round(time.time() - started, 1)}


# ------------------------------------------------------------------ health


class HealthCache:
    """doctor takes a few seconds (it walks scan_roots), so keep its answer for a minute."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._at = 0.0
        self._results: list[tuple[str, str]] = []

    def results(self, force: bool = False) -> tuple[list[tuple[str, str]], float]:
        with self._lock:
            if force or not self._results or time.time() - self._at > HEALTH_TTL:
                self._results, self._at = doctor.results(), time.time()
            return self._results, self._at


def health(log: LogCache, cache: HealthCache, window: str, force: bool = False) -> dict[str, Any]:
    start = window_start(window)
    results, at = cache.results(force)
    last: dict[str, float] = {}
    sessions: Counter = Counter()
    warnings: Counter = Counter()
    for t, rec in log.records():
        agent = rec.get("agent")
        if not agent:
            continue
        last[agent] = t
        if start is not None and t < start:
            continue
        if rec.get("event") == "session_start":
            sessions[agent] += 1
        elif rec.get("event") == "finding":
            warnings[agent] += 1
    rows = []
    listed = set()
    for a in adapters.load_all():
        listed.add(a.name)
        present = adapters.hooks_present(a) if a.detected() else None
        rows.append({"name": a.name, "title": a.title, "installed": a.detected(),
                     "hooks": "none" if a.hooks_writer == "none" else ("wired" if present else
                                                                        "missing" if present is False else "n/a"),
                     "last": last.get(a.name), "sessions": sessions[a.name], "warnings": warnings[a.name]})
    for name in sorted(set(last) - listed):  # e.g. human-or-unknown (commits made outside any agent)
        rows.append({"name": name, "title": name, "installed": None, "hooks": "n/a", "last": last[name],
                     "sessions": sessions[name], "warnings": warnings[name]})
    return {"window": window, "checkedAt": at, "results": [{"level": lvl, "message": m} for lvl, m in results],
            "agents": rows}


# ------------------------------------------------------------------ check


MAX_CHECK_TEXT = 2_000_000


def _finding(f: Finding) -> dict[str, Any]:
    return {**f.to_event(), "title": f.rule.title, "why": f.rule.why, "fix": f.rule.fix, "kind": f.rule.kind,
            "origin": f.rule.origin, "where": f.where}


def check(body: dict[str, Any]) -> dict[str, Any]:
    """The same checks as `guard check`. Nothing is logged and nothing is written."""
    mode = body.get("mode")
    text = body.get("text") or ""
    if mode not in CHECK_MODES:
        raise BadRequest(f"mode must be one of {', '.join(CHECK_MODES)}")
    if not isinstance(text, str) or len(text) > MAX_CHECK_TEXT:
        raise BadRequest("text must be a string of at most 2 MB")
    repo = body.get("repo")
    if repo is not None and not isinstance(repo, str):
        raise BadRequest("repo must be a path")
    root = repo_arg(repo)
    rules_here, project = active_rules(root)
    files_checked: int | None = None
    if mode == "command":
        findings = check_command(rules_here, text)
    elif mode == "message":
        findings = check_commit_message(rules_here, text)
    else:
        if mode == "diff":
            files = parse_unified(text)
        elif not project.root:
            raise BadRequest("pick a git repository to check its changes")
        else:
            files = staged(project.root) if mode == "staged" else working(project.root)
        files_checked = len(files)
        findings = check_files(rules_here, files, project.exclude_paths)
    order = {"hard": 0, "soft": 1}
    findings.sort(key=lambda f: (order.get(f.rule.severity, 2), f.rule.id, f.path or "", f.line or 0))
    return {
        "mode": mode,
        "root": str(project.root) if project.root else None,
        "rootLabel": contract(project.root) if project.root else None,
        "rules": len(rules_here),
        "files": files_checked,
        "disabled": [{"id": k, "reason": v} for k, v in project.disabled.items()],
        "problems": project.problems,
        "findings": [_finding(f) for f in findings if not f.suppressed],
        "kept": [_finding(f) for f in findings if f.suppressed],
        "checkedAt": time.time(),
    }
