"""Run rules against added lines, touched paths, shell commands and commit messages."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from agent_guard.diff import FileDiff
from agent_guard.rules import Rule, path_matches

# An agent (or person) can keep a flagged line by explaining why on that line:
#   foo = bar  # agent-guard: allow no-ts-any reason why this one is fine
ALLOW_RE = re.compile(r"agent-guard:\s*allow\s+([a-z0-9-]+)(?:\s+(\S.*))?")
MAX_SNIPPET = 160


@dataclass
class Finding:
    rule: Rule
    detail: str
    path: str | None = None
    line: int | None = None
    text: str | None = None
    suppressed: str | None = None  # reason given with agent-guard: allow

    @property
    def key(self) -> str:
        """Stable across line shifts, so a warning is not repeated after unrelated edits."""
        basis = f"{self.rule.id}|{self.path}|{(self.text or self.detail).strip()}"
        return hashlib.sha1(basis.encode("utf-8", "replace")).hexdigest()[:16]

    @property
    def where(self) -> str:
        if self.path and self.line:
            return f"{self.path}:{self.line}"
        return self.path or ""

    def snippet(self) -> str:
        t = (self.text or "").strip()
        return t if len(t) <= MAX_SNIPPET else t[: MAX_SNIPPET - 1] + "…"

    def to_event(self) -> dict:
        return {
            "rule": self.rule.id,
            "severity": self.rule.severity,
            "path": self.path,
            "line": self.line,
            "snippet": self.snippet() or self.detail[:MAX_SNIPPET],
            "suppressed": self.suppressed,
        }


def _allowed(rule: Rule, text: str) -> str | None:
    for m in ALLOW_RE.finditer(text):
        if m.group(1) == rule.id:
            return (m.group(2) or "no reason given").strip()
    return None


def _excluded(rule: Rule, path: str, exclude_paths: list[str]) -> bool:
    return not rule.applies(path) or path_matches(path, exclude_paths)


def check_files(rules: list[Rule], files: list[FileDiff], exclude_paths: list[str] | None = None,
                include_size: bool = True) -> list[Finding]:
    exclude_paths = exclude_paths or []
    findings: list[Finding] = []
    for rule in rules:
        kind = rule.kind
        if kind == "added-line-forbidden":
            pats = rule.patterns()
            allow = [re.compile(p) for p in rule.check.get("allow_patterns") or []]
            for f in files:
                if f.binary or f.status == "D" or _excluded(rule, f.path, exclude_paths):
                    continue
                for lineno, text in f.added:
                    if any(p.search(text) for p in pats) and not any(a.search(text) for a in allow):
                        findings.append(Finding(rule, rule.title, f.path, lineno, text, _allowed(rule, text)))
                if rule.check.get("multiline"):
                    block = "\n".join(t for _, t in f.added)
                    for p in pats:
                        m = p.search(block)
                        if m and not any(x.rule is rule and x.path == f.path for x in findings):
                            findings.append(Finding(rule, rule.title, f.path, None, m.group(0)))
        elif kind == "added-line-required":
            pats = rule.patterns()
            for f in files:
                if f.binary or f.status == "D" or not f.added or _excluded(rule, f.path, exclude_paths):
                    continue
                if not any(p.search(t) for _, t in f.added for p in pats):
                    findings.append(Finding(rule, f"{rule.title} (no added line matches)", f.path))
        elif kind == "path-forbidden":
            forbidden = rule.check.get("paths") or []
            allowed = rule.check.get("allow") or []
            for f in files:
                for p in {f.path, f.old_path} - {None, ""}:
                    # exclude_paths only skips file *contents*; path rules are about the paths themselves.
                    if path_matches(p, forbidden) and not path_matches(p, allowed) and not path_matches(p, rule.exclude):
                        findings.append(Finding(rule, f"{rule.title}: {p}", p))
                        break
        elif kind == "diff-size" and include_size:
            counted = [f for f in files if not path_matches(f.path, exclude_paths)
                       and not path_matches(f.path, rule.exclude)]
            n_files = len(counted)
            n_lines = sum(len(f.added) for f in counted)
            max_files = rule.check.get("max_files")
            max_lines = rule.check.get("max_added_lines")
            if (max_files and n_files > max_files) or (max_lines and n_lines > max_lines):
                findings.append(Finding(rule, f"{rule.title}: {n_files} files, {n_lines} added lines"))
    return findings


def check_command(rules: list[Rule], command: str) -> list[Finding]:
    findings: list[Finding] = []
    if not command:
        return findings
    for rule in rules:
        if rule.kind != "command-forbidden":
            continue
        allow = [re.compile(p) for p in rule.check.get("allow_patterns") or []]
        for p in rule.patterns():
            m = p.search(command)
            if m and not any(a.search(command) for a in allow):
                findings.append(Finding(rule, rule.title, text=command, suppressed=_allowed(rule, command)))
                break
    return findings


def commit_subject(message: str, comment_char: str = "#") -> str:
    for line in message.splitlines():
        s = line.strip()
        if s and not s.startswith(comment_char):
            return s
    return ""


def check_commit_message(rules: list[Rule], message: str) -> list[Finding]:
    findings: list[Finding] = []
    subject = commit_subject(message)
    if not subject:
        return findings
    for rule in rules:
        if rule.kind != "commit-message":
            continue
        allow = [re.compile(p) for p in rule.check.get("allow") or []]
        if any(a.search(subject) for a in allow):
            continue
        if not any(p.search(subject) for p in rule.patterns()):
            findings.append(Finding(rule, rule.title, text=subject))
    return findings


def active(findings: list[Finding]) -> list[Finding]:
    return [f for f in findings if not f.suppressed]
