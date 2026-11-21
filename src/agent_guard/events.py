"""Audit log, per-session dedupe state, and macOS notifications."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent_guard import config
from agent_guard.paths import log_file, session_state_dir, state_home

MAX_LOG_BYTES = 20_000_000
MAX_ROTATED_LOGS = 5  # events.<unix-time>.jsonl files kept after rotation (about 100 MB at most)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def log(event: str, **fields: Any) -> None:
    """Append one JSON line to a log only you can read. Logging must never break a hook."""
    try:
        path = log_file()
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        _private(path.parent, 0o700)
        if path.exists() and path.stat().st_size > MAX_LOG_BYTES:
            _rotate(path)
        rec = {"ts": now_iso(), "event": event, **{k: v for k, v in fields.items() if v is not None}}
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        _private(path, 0o600)  # logs written by older versions were readable by every user on the Mac
    except Exception:
        # Logging is best effort: a full disk must never break an agent's hook.
        return


def _private(path: Path, mode: int) -> None:
    if path.stat().st_mode & 0o077:
        os.chmod(path, mode)


def _rotate(path: Path) -> None:
    try:
        path.rename(path.with_suffix(f".{int(time.time())}.jsonl"))
    except FileNotFoundError:
        return  # another hook process rotated it first; append to the fresh file
    for old in _rotated(path)[:-MAX_ROTATED_LOGS]:
        old.unlink(missing_ok=True)


def _rotated(path: Path) -> list[Path]:
    """Rotated copies of the log, oldest first."""
    found: list[tuple[int, Path]] = []
    for p in path.parent.glob(f"{path.stem}.*{path.suffix}"):
        stamp = p.name[len(path.stem) + 1 : -len(path.suffix)]
        if stamp.isdigit():
            found.append((int(stamp), p))
    return [p for _, p in sorted(found)]


def log_files() -> list[Path]:
    """Every log file that still exists, oldest first: rotated copies, then the live log."""
    path = log_file()
    return _rotated(path) + ([path] if path.exists() else [])


def log_signature() -> tuple[tuple[str, int, int], ...]:
    """Changes whenever anything is logged or a log rotates. Cheap: one stat per file."""
    out = []
    for p in log_files():
        try:
            st = p.stat()
        except OSError:
            continue
        out.append((p.name, st.st_size, st.st_mtime_ns))
    return tuple(out)


def read_log(since_ts: float | None = None) -> list[dict[str, Any]]:
    """Log records, oldest first, including rotated logs so a rotation doesn't hide recent history."""
    out: list[dict[str, Any]] = []
    for path in log_files():
        try:
            if since_ts is not None and path.stat().st_mtime < since_ts:
                continue  # last written before the window started, so every record in it is older
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(rec, dict):
                continue
            if since_ts is not None:
                try:
                    if datetime.fromisoformat(rec["ts"]).timestamp() < since_ts:
                        continue
                except (KeyError, TypeError, ValueError):
                    continue
            out.append(rec)
    return out


# ------------------------------------------------------------- session state


class SessionState:
    """Remembers which warnings an agent session already got, and which files it touched."""

    def __init__(self, agent: str, session_id: str | None):
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in (session_id or "nosession"))[:80]
        self.path = session_state_dir() / f"{agent}-{safe}.json"
        self.data: dict[str, Any] = _read_state(self.path)
        self._counts: dict[str, int] = {}
        self._flush: dict[str, Any] | None = None

    def count(self, key: str, n: int = 1) -> None:
        """Add to this session's activity counters (files and commands checked, warnings)."""
        if n:
            self._counts[key] = self._counts.get(key, 0) + n

    def flush_checked(self, **fields: Any) -> None:
        """On save, log the counters as one `checked` record (if anything was checked) and start over.

        Warnings alone can't tell "every edit was clean" from "the hooks never ran"; these records can, without
        logging any content.
        """
        self._flush = fields

    def is_new(self, key: str) -> bool:
        return key not in self.data["seen"]

    def mark(self, key: str) -> None:
        if key not in self.data["seen"]:
            self.data["seen"].append(key)

    def touch(self, root: str, rel: str) -> None:
        lst = self.data["touched"].setdefault(root, [])
        if rel not in lst:
            lst.append(rel)

    def touched(self, root: str) -> list[str]:
        return list(self.data["touched"].get(root, []))

    def save(self) -> None:
        """Merge into what is on disk, then replace the file atomically.

        Agents run hooks for parallel tool calls at the same time. A plain write let one hook read another's
        half-written file (losing every remembered warning) or overwrite marks the other had just saved.
        """
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with _state_lock(self.path.parent):
                disk = _read_state(self.path)
                on_disk = set(disk["seen"])
                seen = disk["seen"] + [k for k in self.data["seen"] if k not in on_disk]
                touched = {root: list(rels) for root, rels in disk["touched"].items()}
                for root, rels in self.data["touched"].items():
                    lst = touched.setdefault(root, [])
                    lst += [r for r in rels if r not in lst]
                merged = {**disk, **self.data, "seen": seen[-2000:], "touched": touched}
                for key in ("last_ts", "flushed_at"):
                    if isinstance(disk.get(key), (int, float)) and isinstance(self.data.get(key), (int, float)):
                        merged[key] = max(disk[key], self.data[key])
                activity = {k: int(v) for k, v in (disk.get("activity") or {}).items() if isinstance(v, (int, float))}
                for k, n in self._counts.items():
                    activity[k] = activity.get(k, 0) + n
                if self._flush is not None:
                    if any(activity.values()):
                        log("checked", **self._flush, **dict(sorted(activity.items())))
                    activity = {}
                    merged["flushed_at"] = time.time()
                merged["activity"] = activity
                tmp = self.path.with_name(f".{self.path.name}.{os.getpid()}.tmp")
                tmp.write_text(json.dumps(merged), encoding="utf-8")
                os.replace(tmp, self.path)
                self.data = merged
                self._counts, self._flush = {}, None
            _prune(self.path.parent)
        except OSError:
            pass


def _read_state(path: Path) -> dict[str, Any]:
    data: Any = {}
    try:
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    if not isinstance(data.get("seen"), list):
        data["seen"] = []
    if not isinstance(data.get("touched"), dict):
        data["touched"] = {}
    return data


@contextmanager
def _state_lock(directory: Path) -> Iterator[None]:
    """An exclusive lock shared by every hook process (POSIX flock; a no-op where fcntl is missing)."""
    try:
        import fcntl
    except ImportError:
        yield
        return
    with (directory / ".lock").open("a") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def _prune(directory: Path, max_age_days: int = 7) -> None:
    cutoff = time.time() - max_age_days * 86400
    try:
        for p in [*directory.glob("*.json"), *directory.glob(".*.tmp")]:  # .tmp: left by a killed hook
            if p.stat().st_mtime < cutoff:
                p.unlink(missing_ok=True)
    except OSError:
        pass


# ------------------------------------------------------------- notifications


def notify(title: str, message: str, key: str = "global", force: bool = False) -> bool:
    """macOS notification, at most one per key per debounce window. Never blocks."""
    cfg = config.load()["notify"]
    if os.environ.get("AGENT_GUARD_NOTIFY", "1") == "0" or not cfg.get("enabled", True):
        return False
    if sys.platform != "darwin":
        return False
    stamp_dir = state_home() / "state" / "notify"
    safe = "".join(c if c.isalnum() else "_" for c in key)[-120:]
    stamp = stamp_dir / safe
    try:
        stamp_dir.mkdir(parents=True, exist_ok=True)
        if not force and stamp.exists() and time.time() - stamp.stat().st_mtime < cfg.get("debounce_seconds", 300):
            return False
        stamp.touch()
        # Text goes in as arguments, not inside the script, so quotes and non-ASCII (·, ⚠) need no escaping.
        subprocess.Popen(["osascript", "-e", "on run argv", "-e",
                          "display notification (item 1 of argv) with title (item 2 of argv)", "-e", "end run",
                          message[:220], title[:80]],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        return True
    except OSError:
        return False
