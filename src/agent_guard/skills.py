"""One skill pool for every agent: ~/.agents/skills.

- Your own skills live in the repo's skills/ folder and are symlinked into the pool.
- Real skill folders found in an agent's own skills dir are imported into the
  pool once (identical copies are just removed; if copies differ, the newer one
  wins and the older one stays in the backups).
- Agents that read the pool natively (Codex, Gemini, OpenCode, DSH) get no
  extra links, and duplicate links into the pool are removed from their dirs.
- Agents that don't (Claude, Cursor, Copilot, Antigravity) get one symlink per skill.

Nothing is deleted without a backup (see state.InstallState).
"""

from __future__ import annotations

import filecmp
import os
import shutil
from pathlib import Path

from agent_guard.adapters import Adapter
from agent_guard.paths import contract, expand, repo_root
from agent_guard.state import InstallState, remove_path, symlink

IGNORE = {".DS_Store"}


def pool() -> Path:
    return expand("~/.agents/skills")


def own_skills() -> dict[str, Path]:
    d = repo_root() / "skills"
    return {p.name: p for p in sorted(d.iterdir()) if (p / "SKILL.md").exists()} if d.is_dir() else {}


def _is_skill_dir(p: Path) -> bool:
    return p.is_dir() and not p.is_symlink() and (p / "SKILL.md").exists() and not p.name.startswith(".")


def same_tree(a: Path, b: Path) -> bool:
    def walk(d: Path) -> dict[str, Path]:
        out = {}
        for root, dirs, files in os.walk(d):
            dirs[:] = [x for x in dirs if x not in IGNORE]
            for f in files:
                if f in IGNORE or ".backup-" in f:
                    continue
                full = Path(root) / f
                out[str(full.relative_to(d))] = full
        return out

    wa, wb = walk(a), walk(b)
    if wa.keys() != wb.keys():
        return False
    return all(filecmp.cmp(wa[k], wb[k], shallow=False) for k in wa)


def _mtime(skill: Path) -> float:
    try:
        return (skill / "SKILL.md").stat().st_mtime
    except OSError:
        return 0.0


def _resolves_into(link: Path, base: Path) -> bool:
    try:
        return link.is_symlink() and (Path(os.readlink(link)).parent == base or link.resolve().parent == base.resolve())
    except OSError:
        return False


def sync(adapters: list[Adapter], state: InstallState, dry_run: bool = False) -> list[str]:
    log: list[str] = []
    P = pool()
    own = own_skills()
    if not dry_run:
        P.mkdir(parents=True, exist_ok=True)

    # 1. your own skills -> pool
    for name, src in own.items():
        link = P / name
        if link.is_symlink() and Path(os.readlink(link)) == src:
            continue
        log.append(f"pool: {name} -> repo skills/{name}")
        if not dry_run:
            symlink(link, src, state)

    # 2. import real skill folders from every agent's own dir
    detected = [a for a in adapters if a.detected()]
    for a in detected:
        for d in map(expand, a.skills_dirs):
            if not d.is_dir():
                continue
            for entry in sorted(d.iterdir()):
                if not _is_skill_dir(entry):
                    continue
                name = entry.name
                target = P / name
                if name in own:
                    log.append(f"{a.name}: {contract(entry)} is a copy of your skill {name}; replaced by the shared one")
                elif target.exists():
                    if same_tree(entry, target):
                        log.append(f"{a.name}: {contract(entry)} is identical to the pool copy; deduplicated")
                    elif _mtime(entry) > _mtime(target):
                        log.append(f"{a.name}: {name} differs from the pool; kept the newer copy from {contract(entry)}")
                        if not dry_run:
                            if not target.is_symlink():
                                state.record(target)
                            remove_path(target)
                            shutil.copytree(entry, target, symlinks=True)
                    else:
                        log.append(f"{a.name}: {name} differs from the pool; kept the newer pool copy, "
                                   f"backed up {contract(entry)}")
                else:
                    log.append(f"{a.name}: moved {name} into the shared pool")
                    if not dry_run:
                        state.record(target)
                        shutil.copytree(entry, target, symlinks=True)
                if not dry_run:
                    state.record(entry)
                    remove_path(entry)

    # 3. link or dedupe per agent
    skills = sorted(p.name for p in P.iterdir() if not p.name.startswith(".") and (P / p.name / "SKILL.md").exists()) \
        if P.is_dir() else sorted(own)
    for a in detected:
        for d in map(expand, a.skills_dirs):
            if a.skills_mode == "link":
                for name in skills:
                    if name in a.skills_exclude:
                        continue
                    link = d / name
                    if link.is_symlink() and (Path(os.readlink(link)) == P / name or link.resolve() == (P / name).resolve()):
                        continue
                    if link.exists() and not link.is_symlink():
                        log.append(f"{a.name}: {contract(link)} is not a skill folder agent-guard manages; left alone")
                        continue
                    log.append(f"{a.name}: linked {name}")
                    if not dry_run:
                        symlink(link, P / name, state)
                if d.is_dir():
                    for link in d.iterdir():
                        if link.is_symlink() and not link.exists() and _resolves_into(link, P):
                            log.append(f"{a.name}: removed broken link {link.name}")
                            if not dry_run:
                                state.record(link)
                                link.unlink()
            elif a.skills_mode == "native" and d.is_dir():
                for link in sorted(d.iterdir()):
                    if _resolves_into(link, P):
                        log.append(f"{a.name}: removed duplicate link {link.name} (it reads ~/.agents/skills itself)")
                        if not dry_run:
                            state.record(link)
                            link.unlink()
    return log


def status(adapters: list[Adapter]) -> list[tuple[str, str]]:
    """(level, message) pairs for doctor."""
    out: list[tuple[str, str]] = []
    P = pool()
    for name, src in own_skills().items():
        link = P / name
        if not (link.is_symlink() and Path(os.readlink(link)) == src):
            out.append(("fail", f"skill {name} is not linked into {contract(P)}"))
    names = sorted(p.name for p in P.iterdir() if (p / "SKILL.md").exists()) if P.is_dir() else []
    for a in adapters:
        if not a.detected():
            continue
        for d in map(expand, a.skills_dirs):
            if not d.is_dir():
                continue
            stray = [e.name for e in d.iterdir() if _is_skill_dir(e)]
            if stray:
                out.append(("warn", f"{a.name}: unshared skill folders in {contract(d)}: {', '.join(stray)} "
                                    "(run `guard install` to share them)"))
            if a.skills_mode == "link":
                missing = [n for n in names if n not in a.skills_exclude and not (d / n).exists()]
                if missing:
                    out.append(("fail", f"{a.name}: missing skill links in {contract(d)}: {', '.join(missing)}"))
    return out
