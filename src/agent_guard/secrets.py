"""Secret scanning via gitleaks (https://github.com/gitleaks/gitleaks) when installed.

Our own `no-secrets` regex rule stays as the fast check on every edit; gitleaks
adds its much larger, maintained ruleset at commit time and in `guard scan`.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from agent_guard.checks import Finding
from agent_guard.rules import Rule


def available() -> bool:
    return shutil.which("gitleaks") is not None


def _run(args: list[str], cwd: Path) -> list[dict]:
    try:
        r = subprocess.run(["gitleaks", *args, "--no-banner", "--redact", "--exit-code", "0",
                            "--report-format", "json", "--report-path", "-"],
                           cwd=str(cwd), capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return []
    out = r.stdout.strip()
    if not out.startswith("["):
        return []
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return []


def _to_findings(items: list[dict], rule: Rule) -> list[Finding]:
    found = []
    for it in items:
        path = it.get("File") or ""
        detail = f"{rule.title} (gitleaks: {it.get('RuleID', 'secret')})"
        found.append(Finding(rule, detail, path, it.get("StartLine"), it.get("Match") or it.get("Description")))
    return found


def staged(root: Path, rule: Rule) -> list[Finding]:
    return _to_findings(_run(["git", "--staged"], root), rule)


def directory(root: Path, rule: Rule) -> list[Finding]:
    return _to_findings(_run(["dir", "."], root), rule)


def merge(base: list[Finding], extra: list[Finding]) -> list[Finding]:
    """Add gitleaks findings that our regex did not already report at the same place."""
    seen = {(f.rule.id, f.path, f.line) for f in base}
    return base + [f for f in extra if (f.rule.id, f.path, f.line) not in seen]
