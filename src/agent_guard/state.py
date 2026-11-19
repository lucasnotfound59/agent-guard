"""Install state: the original version of every path agent-guard ever touched.

The first time a path is touched we save what was there (missing, a file, a
symlink, or a directory) under ~/.agent-guard/backups/. Later installs never
overwrite that record, so `guard uninstall` can always put things back.
"""

from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path
from typing import Any

from agent_guard.paths import contract, state_home, user_home


class InstallState:
    def __init__(self) -> None:
        self.path = state_home() / "install-state.json"
        self.data: dict[str, Any] = {"originals": {}, "created_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
        if self.path.exists():
            self.data = json.loads(self.path.read_text(encoding="utf-8"))
        self.data.setdefault("originals", {})
        self._backup_root = state_home() / "backups" / time.strftime("%Y%m%d-%H%M%S")

    @property
    def originals(self) -> dict[str, dict[str, Any]]:
        return self.data["originals"]

    def record(self, path: Path) -> None:
        key = str(path)
        if key in self.originals:
            return
        if path.is_symlink():
            self.originals[key] = {"type": "symlink", "target": os.readlink(path)}
        elif path.is_dir():
            dest = self._backup_dest(path)
            shutil.copytree(path, dest, symlinks=True)
            self.originals[key] = {"type": "dir", "backup": str(dest)}
        elif path.exists():
            dest = self._backup_dest(path)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, dest)
            self.originals[key] = {"type": "file", "backup": str(dest)}
        else:
            created, parent = [], path.parent
            while not parent.exists() and parent != parent.parent:
                created.append(str(parent))  # folders we'll create; removed again on uninstall if empty
                parent = parent.parent
            self.originals[key] = {"type": "missing", "created_dirs": created}
        self.save()

    def _backup_dest(self, path: Path) -> Path:
        try:
            rel = path.relative_to(user_home())
        except ValueError:
            rel = Path(str(path).lstrip("/"))
        return self._backup_root / rel

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, indent=2), encoding="utf-8")

    def restore(self, key: str) -> str:
        """Put one path back the way it was. Returns a short description."""
        orig = self.originals[key]
        path = Path(key)
        remove_path(path)
        t = orig["type"]
        if t == "missing":
            for d in orig.get("created_dirs", []):
                try:
                    Path(d).rmdir()
                except OSError:
                    break  # not empty (something else lives there now): keep it
            msg = f"removed {contract(path)}"
        elif t == "symlink":
            path.parent.mkdir(parents=True, exist_ok=True)
            os.symlink(orig["target"], path)
            msg = f"restored symlink {contract(path)}"
        elif t == "dir":
            shutil.copytree(orig["backup"], path, symlinks=True)
            msg = f"restored folder {contract(path)}"
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(orig["backup"], path)
            msg = f"restored file {contract(path)}"
        del self.originals[key]
        self.save()
        return msg


def remove_path(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


def symlink(link: Path, target: Path, state: InstallState) -> bool:
    """Point link at target, recording the original. Returns True if anything changed."""
    if link.is_symlink() and Path(os.readlink(link)) == target:
        return False
    state.record(link)
    remove_path(link)
    link.parent.mkdir(parents=True, exist_ok=True)
    os.symlink(target, link)
    return True
