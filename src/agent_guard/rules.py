"""Rules as data: load, validate, and pick the ones that apply to a repo.

A rule file holds one rule (YAML mapping). Global rules live in the regulation
repo under rules/active; a project can add or disable rules in
.agent-guard.yaml at its git root (every disable needs a reason).
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

from agent_guard.paths import repo_root

KINDS = {
    "added-line-forbidden",
    "added-line-required",
    "path-forbidden",
    "command-forbidden",
    "commit-message",
    "diff-size",
}
SEVERITIES = {"hard", "soft"}
STATUSES = {"candidate", "active", "retired", "rejected"}
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}$")
PROJECT_FILE = ".agent-guard.yaml"


# ---------------------------------------------------------------- globbing


@lru_cache(maxsize=512)
def _glob_re(pattern: str) -> re.Pattern[str]:
    """gitignore-flavoured glob: `**` spans directories, `*` stays inside one."""
    i, out = 0, []
    while i < len(pattern):
        c = pattern[i]
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif c == "*":
            out.append("[^/]*")
            i += 1
        elif c == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(c))
            i += 1
    return re.compile("^" + "".join(out) + "$")


def path_matches(path: str, patterns: list[str]) -> bool:
    """Patterns without a slash match the file name anywhere (like .gitignore)."""
    p = path.replace("\\", "/")
    while p.startswith("./"):
        p = p[2:]
    name = PurePosixPath(p).name
    for pat in patterns or []:
        target = p if "/" in pat else name
        if _glob_re(pat).match(target):
            return True
    return False


# ---------------------------------------------------------------- model


@dataclass
class Rule:
    id: str
    title: str
    severity: str
    status: str
    check: dict[str, Any]
    why: str = ""
    fix: str = ""
    applies_to: list[str] = field(default_factory=lambda: ["**"])
    exclude: list[str] = field(default_factory=list)
    source: dict[str, Any] = field(default_factory=dict)
    file: Path | None = None
    origin: str = "global"  # global | project

    @property
    def kind(self) -> str:
        return str(self.check.get("kind", ""))

    def patterns(self) -> list[re.Pattern[str]]:
        raw = self.check.get("patterns") or ([self.check["pattern"]] if self.check.get("pattern") else [])
        flags = re.IGNORECASE if self.check.get("ignore_case") else 0
        return [re.compile(p, flags) for p in raw]

    def applies(self, path: str) -> bool:
        return path_matches(path, self.applies_to) and not path_matches(path, self.exclude)

    @classmethod
    def from_dict(cls, data: dict[str, Any], file: Path | None = None, origin: str = "global") -> "Rule":
        def as_list(v: Any) -> list[str]:
            if v is None:
                return []
            return [v] if isinstance(v, str) else list(v)

        return cls(
            id=str(data.get("id", "")),
            title=str(data.get("title", "")),
            severity=str(data.get("severity", "soft")),
            status=str(data.get("status", "active")),
            check=dict(data.get("check") or {}),
            why=str(data.get("why", "")).strip(),
            fix=str(data.get("fix", "")).strip(),
            applies_to=as_list(data.get("applies_to")) or ["**"],
            exclude=as_list(data.get("exclude")),
            source=dict(data.get("source") or {}),
            file=file,
            origin=origin,
        )

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "id": self.id,
            "title": self.title,
            "severity": self.severity,
            "status": self.status,
            "applies_to": self.applies_to,
        }
        if self.exclude:
            d["exclude"] = self.exclude
        d["check"] = self.check
        d["why"] = self.why
        d["fix"] = self.fix
        if self.source:
            d["source"] = self.source
        return d


def validate(rule: Rule) -> list[str]:
    errs: list[str] = []
    if not ID_RE.match(rule.id):
        errs.append(f"id {rule.id!r} must be kebab-case (a-z, 0-9, -)")
    if not rule.title:
        errs.append("title is required")
    if rule.severity not in SEVERITIES:
        errs.append(f"severity must be one of {sorted(SEVERITIES)}")
    if rule.status not in STATUSES:
        errs.append(f"status must be one of {sorted(STATUSES)}")
    if rule.kind not in KINDS:
        errs.append(f"check.kind must be one of {sorted(KINDS)}")
    if not rule.why:
        errs.append("why is required (agents need the reason)")
    if not rule.fix:
        errs.append("fix is required (agents need to know what to do instead)")
    if rule.kind in {"added-line-forbidden", "added-line-required", "command-forbidden", "commit-message"}:
        if not (rule.check.get("pattern") or rule.check.get("patterns")):
            errs.append(f"{rule.kind} needs check.pattern or check.patterns")
        try:
            rule.patterns()
        except re.error as e:
            errs.append(f"bad regex: {e}")
        for extra in ("allow", "allow_patterns"):
            for p in rule.check.get(extra) or []:
                try:
                    re.compile(p)
                except re.error as e:
                    errs.append(f"bad regex in check.{extra}: {e}")
    if rule.kind == "path-forbidden" and not rule.check.get("paths"):
        errs.append("path-forbidden needs check.paths")
    if rule.kind == "diff-size" and not (rule.check.get("max_files") or rule.check.get("max_added_lines")):
        errs.append("diff-size needs check.max_files or check.max_added_lines")
    return errs


# ---------------------------------------------------------------- loading


def load_dir(directory: Path, origin: str = "global") -> list[Rule]:
    rules: list[Rule] = []
    if not directory.is_dir():
        return rules
    for f in sorted(directory.glob("*.yaml")):
        data = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        rules.append(Rule.from_dict(data, file=f, origin=origin))
    return rules


def global_rules(status: str = "active") -> list[Rule]:
    sub = {"active": "active", "candidate": "candidates", "retired": "retired"}[status]
    return load_dir(repo_root() / "rules" / sub)


def find_repo_root(cwd: str | Path | None) -> Path | None:
    if not cwd:
        return None
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    return Path(out.stdout.strip())


@dataclass
class ProjectConfig:
    root: Path | None
    disabled: dict[str, str] = field(default_factory=dict)  # id -> reason
    rules: list[Rule] = field(default_factory=list)
    exclude_paths: list[str] = field(default_factory=list)
    checks: list[dict[str, Any]] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)


def load_project(root: Path | None) -> ProjectConfig:
    cfg = ProjectConfig(root=root)
    if not root:
        return cfg
    f = root / PROJECT_FILE
    if not f.exists():
        return cfg
    try:
        data = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        cfg.problems.append(f"{PROJECT_FILE}: invalid YAML ({e})")
        return cfg
    for item in data.get("disable") or []:
        if isinstance(item, dict) and item.get("id"):
            reason = str(item.get("reason", "")).strip()
            if reason:
                cfg.disabled[str(item["id"])] = reason
            else:
                cfg.problems.append(f"{PROJECT_FILE}: disable {item['id']} has no reason, so it is ignored")
        else:
            cfg.problems.append(f"{PROJECT_FILE}: disable entries need an id and a reason")
    for raw in data.get("rules") or []:
        r = Rule.from_dict(raw, file=f, origin="project")
        errs = validate(r)
        if errs:
            cfg.problems.append(f"{PROJECT_FILE}: rule {r.id or '?'}: {'; '.join(errs)}")
        elif r.status == "active":
            cfg.rules.append(r)
    cfg.exclude_paths = [str(p) for p in data.get("exclude_paths") or []]
    cfg.checks = [c for c in data.get("checks") or [] if isinstance(c, dict) and c.get("run")]
    return cfg


def active_rules(cwd: str | Path | None) -> tuple[list[Rule], ProjectConfig]:
    """Global active rules plus project rules, minus project disables."""
    project = load_project(find_repo_root(cwd))
    rules = [r for r in global_rules("active") if r.status == "active" and not validate(r)]
    rules = [r for r in rules if r.id not in project.disabled]
    ids = {r.id for r in rules}
    rules += [r for r in project.rules if r.id not in ids]
    return rules, project
