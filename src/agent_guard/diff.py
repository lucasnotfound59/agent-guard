"""Turn git changes (or raw edit text) into per-file added lines.

Only added lines are ever checked, so old code in a repo never nags; agents
just can't add more of it.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
MAX_UNTRACKED_BYTES = 1_000_000
HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


@dataclass
class FileDiff:
    path: str  # new path, repo-relative ("" for deletions uses old path)
    status: str = "M"  # A added, M modified, D deleted, R renamed
    old_path: str | None = None
    added: list[tuple[int | None, str]] = field(default_factory=list)
    binary: bool = False


def _git(args: list[str], cwd: Path, timeout: int = 20) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-c", "core.quotepath=false", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        errors="replace",
        timeout=timeout,
    )


def _unquote(p: str) -> str:
    if len(p) >= 2 and p[0] == '"' and p[-1] == '"':
        p = p[1:-1].encode("utf-8").decode("unicode_escape").encode("latin-1").decode("utf-8", "replace")
    return p


def parse_unified(text: str) -> list[FileDiff]:
    files: list[FileDiff] = []
    cur: FileDiff | None = None
    new_line: int | None = None
    in_hunk = False
    for line in text.splitlines():
        if line.startswith("diff --git "):
            cur = FileDiff(path="")
            files.append(cur)
            in_hunk = False
            m = re.match(r'^diff --git (?:"?a/(.+?)"?) (?:"?b/(.+?)"?)$', line)
            if m:
                cur.old_path, cur.path = m.group(1), m.group(2)
            continue
        if cur is None:
            continue
        if in_hunk and line[:1] in ("+", "-", " ", "\\"):
            if line.startswith("+"):
                cur.added.append((new_line, line[1:]))
                if new_line is not None:
                    new_line += 1
            elif line.startswith(" "):
                if new_line is not None:
                    new_line += 1
            continue
        in_hunk = False
        if line.startswith("@@"):
            m = HUNK_RE.match(line)
            new_line = int(m.group(1)) if m else None
            in_hunk = True
        elif line.startswith("new file mode"):
            cur.status = "A"
        elif line.startswith("deleted file mode"):
            cur.status = "D"
        elif line.startswith("rename from "):
            cur.old_path = _unquote(line[len("rename from ") :])
            cur.status = "R"
        elif line.startswith("rename to "):
            cur.path = _unquote(line[len("rename to ") :])
            cur.status = "R"
        elif line.startswith("--- "):
            src = _unquote(line[4:])
            if src != "/dev/null":
                cur.old_path = src[2:] if src.startswith("a/") else src
        elif line.startswith("+++ "):
            dst = line[4:]
            if dst == "/dev/null":
                cur.status = "D"
                cur.path = cur.old_path or cur.path
            else:
                d = _unquote(dst)
                cur.path = d[2:] if d.startswith("b/") else d
        elif line.startswith("Binary files") or line.startswith("GIT binary patch"):
            cur.binary = True
    for f in files:
        if not f.path:
            f.path = f.old_path or ""
    return files


DIFF_FLAGS = ["--no-color", "--no-ext-diff", "-U0", "-M", "--src-prefix=a/", "--dst-prefix=b/"]


def has_head(root: Path) -> bool:
    return _git(["rev-parse", "--verify", "-q", "HEAD"], root).returncode == 0


def staged(root: Path) -> list[FileDiff]:
    out = _git(["diff", "--cached", *DIFF_FLAGS], root)
    return parse_unified(out.stdout)


def working(root: Path, paths: list[str] | None = None) -> list[FileDiff]:
    """HEAD -> working tree (staged + unstaged), plus untracked files."""
    base = "HEAD" if has_head(root) else EMPTY_TREE
    args = ["diff", base, *DIFF_FLAGS]
    if paths is not None:
        if not paths:
            return []
        args += ["--", *paths]
    files = parse_unified(_git(args, root).stdout)
    files += untracked(root, paths)
    return files


def untracked(root: Path, paths: list[str] | None = None) -> list[FileDiff]:
    args = ["ls-files", "--others", "--exclude-standard", "-z"]
    if paths is not None:
        if not paths:
            return []
        args += ["--", *paths]
    out = _git(args, root)
    result = []
    for rel in filter(None, out.stdout.split("\0")):
        result.append(whole_file(root, rel, status="A"))
    return result


def whole_file(root: Path, rel: str, status: str = "A") -> FileDiff:
    fd = FileDiff(path=rel, status=status)
    p = root / rel
    try:
        if p.is_dir() or p.stat().st_size > MAX_UNTRACKED_BYTES:
            fd.binary = True
            return fd
        data = p.read_bytes()
    except OSError:
        return fd
    if b"\0" in data[:8000]:
        fd.binary = True
        return fd
    text = data.decode("utf-8", "replace")
    fd.added = [(i, line) for i, line in enumerate(text.splitlines(), start=1)]
    return fd


def from_text(path: str, text: str) -> FileDiff:
    """Edit content when there is no git repo to diff against (no line numbers)."""
    return FileDiff(path=path, status="M", added=[(None, line) for line in text.splitlines()])


def changed_paths(root: Path) -> list[str]:
    """Paths that differ from HEAD (tracked) or are untracked, repo-relative."""
    out = _git(["status", "--porcelain=v1", "-z", "--untracked-files=all"], root)
    paths: list[str] = []
    entries = out.stdout.split("\0")
    i = 0
    while i < len(entries):
        e = entries[i]
        if not e:
            i += 1
            continue
        code, rel = e[:2], e[3:]
        paths.append(rel)
        if "R" in code or "C" in code:
            i += 1  # skip the original name of a rename/copy
        i += 1
    return paths


PATCH_FILE_RE = re.compile(r"^\*\*\* (Add|Update|Delete) File: (.+)$")
PATCH_MOVE_RE = re.compile(r"^\*\*\* Move to: (.+)$")


def parse_apply_patch(patch: str) -> tuple[list[str], dict[str, list[str]]]:
    """Codex apply_patch envelope -> (touched paths, added lines per path)."""
    paths: list[str] = []
    added: dict[str, list[str]] = {}
    current: str | None = None
    for line in patch.splitlines():
        m = PATCH_FILE_RE.match(line)
        if m:
            action, p = m.group(1), m.group(2).strip()
            current = None if action == "Delete" else p
            if current:
                paths.append(current)
                added.setdefault(current, [])
            continue
        m = PATCH_MOVE_RE.match(line)
        if m and current:
            new = m.group(1).strip()
            added[new] = added.pop(current, [])
            paths[paths.index(current)] = new
            current = new
            continue
        if current and line.startswith("+") and not line.startswith("+++"):
            added[current].append(line[1:])
    return paths, added
