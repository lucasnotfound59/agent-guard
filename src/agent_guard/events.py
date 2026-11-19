"""Audit log, per-session dedupe state, and macOS notifications."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent_guard import config
from agent_guard.paths import log_file, session_state_dir, state_home

MAX_LOG_BYTES = 20_000_000


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def log(event: str, **fields: Any) -> None:
    """Append one JSON line. Logging must never break a hook."""
    try:
        path = log_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.stat().st_size > MAX_LOG_BYTES:
            path.rename(path.with_suffix(f".{int(time.time())}.jsonl"))
        rec = {"ts": now_iso(), "event": event, **{k: v for k, v in fields.items() if v is not None}}
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        # Logging is best effort: a full disk must never break an agent's hook.
        return


def read_log(since_ts: float | None = None) -> list[dict[str, Any]]:
    path = log_file()
    out: list[dict[str, Any]] = []
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if since_ts is not None:
            try:
                if datetime.fromisoformat(rec["ts"]).timestamp() < since_ts:
                    continue
            except (KeyError, ValueError):
                continue
        out.append(rec)
    return out


# ------------------------------------------------------------- session state


class SessionState:
    """Remembers which warnings an agent session already got, and which files it touched."""

    def __init__(self, agent: str, session_id: str | None):
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in (session_id or "nosession"))[:80]
        self.path = session_state_dir() / f"{agent}-{safe}.json"
        self.data: dict[str, Any] = {"seen": [], "touched": {}}
        try:
            if self.path.exists():
                self.data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
        self.data.setdefault("seen", [])
        self.data.setdefault("touched", {})

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
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.data["seen"] = self.data["seen"][-2000:]
            self.path.write_text(json.dumps(self.data), encoding="utf-8")
            _prune(self.path.parent)
        except OSError:
            pass


def _prune(directory: Path, max_age_days: int = 7) -> None:
    cutoff = time.time() - max_age_days * 86400
    try:
        for p in directory.glob("*.json"):
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
