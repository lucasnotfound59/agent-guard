"""The dashboard's Toolbox: every agent's skills, MCP servers and plugins in one place, plus AGENTS.md.

Changes are allowed from the dashboard (the user chose this), so every change:
- backs up each file or folder first, under ~/.agent-guard/backups/<time>-toolbox/;
- refuses to write a config that changed since the page loaded it (the page sends the version it saw);
- replaces files atomically and keeps their permissions;
- is logged as a `toolbox` event, so it shows in Activity and `guard report`.
agent-guard's own skill and OpenCode plugin can't be switched off or removed here. Secret values (env,
headers, token-like args) never leave the server: the page sees names only.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import threading
import time
from pathlib import Path
from typing import Any

from agent_guard import adapters, events, overrides, skills
from agent_guard.dashboard import mcpconf
from agent_guard.dashboard.data import BadRequest
from agent_guard.gitcmd import detect_agent
from agent_guard.paths import contract, expand, repo_root, state_home, user_home

LOCKED_SKILLS = {"agent-guard"}
LOCKED_PLUGIN_FILES = {"agent-guard.js"}
SKILL_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,80}$")
MAX_TEXT = 512 * 1024
_LOCK = threading.Lock()


class Conflict(BadRequest):
    """The file changed since the page loaded it."""


# ------------------------------------------------------------------ files


def version(path: Path) -> str:
    try:
        return hashlib.sha1(path.read_bytes()).hexdigest()[:16]
    except FileNotFoundError:
        return "missing"
    except OSError:
        return "unreadable"


def _check(path: Path, versions: dict[str, str]) -> None:
    label = contract(path)
    seen = versions.get(label)
    if seen is None:
        raise BadRequest(f"reload the page: it didn't say which version of {label} it saw")
    if seen != version(path):
        raise Conflict(f"{label} changed since the page loaded it (another agent or editor wrote to it). "
                       "Reload, check it, and try again")


class _Backup:
    def __init__(self) -> None:
        self.root = state_home() / "backups" / f"{time.strftime('%Y%m%d-%H%M%S')}-toolbox-{time.time_ns() % 10**6:06d}"
        self.saved: list[str] = []

    def save(self, path: Path) -> None:
        if not (path.exists() or path.is_symlink()):
            return
        try:
            rel = path.relative_to(user_home())
        except ValueError:
            rel = Path(str(path).lstrip("/"))
        dest = self.root / rel
        if dest.exists() or dest.is_symlink():
            return
        dest.parent.mkdir(parents=True, exist_ok=True)
        if path.is_symlink():
            os.symlink(os.readlink(path), dest)
        elif path.is_dir():
            shutil.copytree(path, dest, symlinks=True)
        else:
            shutil.copy2(path, dest)
        self.saved.append(contract(path))


def _write(path: Path, text: str, versions: dict[str, str] | None, backup: _Backup) -> None:
    if versions is not None:
        _check(path, versions)
    backup.save(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = path.stat().st_mode & 0o777 if path.exists() else 0o600
    tmp = path.with_name(f".{path.name}.{os.getpid()}.agent-guard.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
    if versions is not None and versions.get(contract(path)) != version(path):
        tmp.unlink(missing_ok=True)  # written to in the last moment (agents rewrite their own configs often)
        raise Conflict(f"{contract(path)} changed while saving; nothing was written. Reload and try again")
    os.replace(tmp, path)


def _log(action: str, **fields: Any) -> None:
    events.log("toolbox", action=action, by=detect_agent(), **fields)


# ------------------------------------------------------------------ masking


_SECRET_FLAG = re.compile(r"(?i)^--?[\w-]*(?:key|token|secret|passw|auth|credential)[\w-]*$")
_SECRET_VALUE = re.compile(r"^(?:sk-|ghp_|gho_|github_pat_|xox[abp]-|AKIA|AIza)|^(?=.*\d)(?=.*[A-Za-z])[A-Za-z0-9_\-]{28,}$")


def _mask_args(args: list[str]) -> list[str]:
    out, hide_next = [], False
    for a in args:
        key, eq, val = a.partition("=")
        if hide_next or _SECRET_VALUE.search(a):
            out.append("••••")
        elif eq and _SECRET_FLAG.match(key):
            out.append(f"{key}=••••")
        else:
            out.append(a.replace(str(user_home()), "~"))
        hide_next = bool(_SECRET_FLAG.match(a)) and not eq
    return out


def _mask_url(url: str) -> str:
    url = re.sub(r"//[^/@\s]+@", "//••••@", url)
    return re.sub(r"(?i)([?&][^=&]*(?:key|token|secret|auth|sig)[^=&]*=)[^&]*", r"\1••••", url)


def masked(s: dict[str, Any]) -> dict[str, Any]:
    return {
        "transport": s["transport"],
        "command": contract(s["command"]) if s["command"].startswith("/") else s["command"],
        "args": _mask_args(s["args"]),
        "cwd": contract(s["cwd"]) if s.get("cwd") else "",
        "url": _mask_url(s["url"]),
        "env": sorted(s["env"]),
        "headers": sorted(s["headers"]),
    }


# ------------------------------------------------------------------ inventory


def _installed() -> dict[str, adapters.Adapter]:
    return {a.name: a for a in adapters.load_all() if a.detected()}


def _mcp(installed: dict[str, adapters.Adapter], ov: dict[str, Any]) -> dict[str, Any]:
    targets = [t for t in mcpconf.TARGETS if t.agent in installed]
    rows: dict[str, dict[str, Any]] = {}
    neutral: dict[str, dict[str, dict[str, Any]]] = {}
    errors = {}
    for t in targets:
        try:
            found = mcpconf.servers(t)
        except mcpconf.ConfigError as e:
            errors[t.agent] = str(e)
            continue
        parked = ov["mcp_parked"].get(t.agent, {})
        for name, native in [*found.items(), *parked.items()]:
            state = "off" if name in parked or (t.dialect == "opencode" and native.get("enabled") is False) else "on"
            row = rows.setdefault(name, {"name": name, "agents": {}})
            row["agents"][t.agent] = state
            neutral.setdefault(name, {})[t.agent] = mcpconf.to_neutral(t.dialect, native)
    for name, row in rows.items():
        defs = list(neutral[name].values())
        row["server"] = masked(defs[0])
        row["differs"] = any(not mcpconf.same_server(defs[0], d) for d in defs[1:])
        row["definedBy"] = {a: masked(d) for a, d in neutral[name].items()} if row["differs"] else {}
    return {
        "agents": [{"name": t.agent, "title": installed[t.agent].title, "path": t.path,
                    "exists": t.file.exists(), "error": errors.get(t.agent)} for t in targets],
        "servers": sorted(rows.values(), key=lambda r: r["name"].lower()),
    }


def _description(skill_dir: Path) -> str:
    try:
        text = (skill_dir / "SKILL.md").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    m = re.match(r"^---\s*\n(.*?)\n---", text, re.S)
    if not m:
        return ""
    d = re.search(r"^description:\s*(.+?)(?=^\w[\w-]*:|\Z)", m.group(1), re.S | re.M)
    if not d:
        return ""
    value = " ".join(d.group(1).split()).strip().lstrip(">|-").strip().strip("'\"")
    return value[:400]


def _skill_dirs(a: adapters.Adapter) -> list[Path]:
    return [expand(d) for d in a.skills_dirs]


def _skills(installed: dict[str, adapters.Adapter], ov: dict[str, Any]) -> dict[str, Any]:
    pool = skills.pool()
    own = skills.own_skills()
    parked_names = set(ov["skills_parked"])
    agents = [a for a in installed.values() if a.skills_mode in ("link", "native")]
    rows: dict[str, dict[str, Any]] = {}

    def add(name: str, kind: str, where: Path, **extra: Any) -> None:
        rows[name] = {"name": name, "kind": kind, "path": contract(where), "description": _description(where),
                      "locked": name in LOCKED_SKILLS, "agents": {}, **extra}

    if pool.is_dir():
        for p in sorted(pool.iterdir()):
            if p.name.startswith(".") or not (p / "SKILL.md").exists():
                continue
            kind = "own" if p.name in own and p.is_symlink() else "shared"
            add(p.name, kind, p)
    for name in sorted(parked_names):
        where = own.get(name) or overrides.parked_skills_dir() / name
        if where.exists() and name not in rows:
            add(name, "own" if name in own else "shared", where, parked=True)
    for name, row in rows.items():
        for a in agents:
            if row.get("parked"):
                row["agents"][a.name] = "off-all"
            elif a.skills_mode == "native":
                row["agents"][a.name] = "pool"
            elif name in a.skills_exclude:
                row["agents"][a.name] = "excluded"
            elif name in ov["skills_off"].get(a.name, []):
                row["agents"][a.name] = "off"
            else:
                row["agents"][a.name] = "on" if any((d / name).exists() for d in _skill_dirs(a)) else "missing"
    unshared = []
    for a in agents:
        for d in _skill_dirs(a):
            if not d.is_dir():
                continue
            for e in sorted(d.iterdir()):
                if skills._is_skill_dir(e):
                    unshared.append({"name": e.name, "agent": a.name, "path": contract(e),
                                     "description": _description(e), "inPool": e.name in rows})
    return {
        "agents": [{"name": a.name, "title": a.title, "mode": a.skills_mode} for a in agents],
        "skills": list(rows.values()),
        "unshared": unshared,
        "pool": contract(pool),
    }


def _claude_installed() -> list[str]:
    import json

    try:
        data = json.loads(expand("~/.claude/plugins/installed_plugins.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    plugins = data.get("plugins", data) if isinstance(data, dict) else {}
    return [str(k) for k in plugins] if isinstance(plugins, dict) else []


def _plugins(installed: dict[str, adapters.Adapter]) -> dict[str, Any]:
    import json

    out: list[dict[str, Any]] = []
    errors: dict[str, str] = {}
    if "codex" in installed:
        try:
            data, _ = mcpconf.read(mcpconf.BY_AGENT["codex"])
            for pid, cfg in ((data or {}).get("plugins") or {}).items():
                if isinstance(cfg, dict):
                    out.append({"agent": "codex", "id": pid, "enabled": cfg.get("enabled", True) is not False,
                                "kind": "config", "canToggle": True, "canRemove": False})
        except mcpconf.ConfigError as e:
            errors["codex"] = str(e)
    if "claude" in installed:
        f = expand("~/.claude/settings.json")
        try:
            enabled = (json.loads(f.read_text(encoding="utf-8")) or {}).get("enabledPlugins") or {}
        except FileNotFoundError:
            enabled = {}
        except (OSError, ValueError):
            enabled, errors["claude"] = {}, "can't read ~/.claude/settings.json"
        for pid in sorted(set(enabled) | set(_claude_installed())):
            out.append({"agent": "claude", "id": pid, "enabled": bool(enabled.get(pid, False)), "kind": "settings",
                        "canToggle": True, "canRemove": pid in enabled})
    if "opencode" in installed:
        try:
            data, _ = mcpconf.read(mcpconf.BY_AGENT["opencode"])
            for spec in (data or {}).get("plugin") or []:
                out.append({"agent": "opencode", "id": str(spec), "enabled": True, "kind": "npm",
                            "canToggle": False, "canRemove": True})
        except mcpconf.ConfigError as e:
            errors["opencode"] = str(e)
        d = expand("~/.config/opencode/plugins")
        if d.is_dir():
            for f in sorted(d.iterdir()):
                on = f.suffix in (".js", ".ts", ".mjs")
                off = f.name.endswith((".js.off", ".ts.off", ".mjs.off"))
                if on or off:
                    locked = f.name.removesuffix(".off") in LOCKED_PLUGIN_FILES
                    out.append({"agent": "opencode", "id": f.name.removesuffix(".off"), "enabled": on, "kind": "file",
                                "canToggle": not locked, "canRemove": not locked, "locked": locked})
    if "gemini" in installed:
        d = expand("~/.gemini/extensions")
        if d.is_dir():
            for e in sorted(d.iterdir()):
                if (e / "gemini-extension.json").exists():
                    out.append({"agent": "gemini", "id": e.name, "enabled": None, "kind": "extension",
                                "canToggle": False, "canRemove": False})
    titles = {a: installed[a].title for a in installed}
    return {"plugins": out, "errors": errors, "titles": titles}


def _config_files() -> list[Path]:
    files = [t.file for t in mcpconf.TARGETS]
    files += [expand("~/.claude/settings.json"), agents_md_path()]
    return files


def watched_files() -> list[Path]:
    """Files and folders whose change should refresh an open Toolbox."""
    return [*_config_files(), skills.pool(), overrides.path(), overrides.parked_skills_dir(),
            expand("~/.config/opencode/plugins")]


def inventory() -> dict[str, Any]:
    installed = _installed()
    ov = overrides.load()
    return {
        "mcp": _mcp(installed, ov),
        "skills": _skills(installed, ov),
        "plugins": _plugins(installed),
        "versions": {contract(f): version(f) for f in _config_files()},
    }


# ------------------------------------------------------------------ text editors (skills, AGENTS.md)


def agents_md_path() -> Path:
    return repo_root() / "global" / "AGENTS.md"


def _skill_file(name: str, agent: str | None = None) -> Path:
    if not SKILL_NAME.match(name or ""):
        raise BadRequest("not a skill name")
    if agent:
        a = _installed().get(agent)
        if a is None:
            raise BadRequest(f"{agent} isn't an installed agent")
        for d in _skill_dirs(a):
            if skills._is_skill_dir(d / name):
                return d / name / "SKILL.md"
        raise BadRequest(f"{agent} has no skill folder {name}")
    for base in (skills.pool() / name, skills.own_skills().get(name), overrides.parked_skills_dir() / name):
        if base is not None and (base / "SKILL.md").exists():
            return (base / "SKILL.md").resolve()
    raise BadRequest(f"no skill {name}")


def read_text(kind: str, name: str = "", agent: str = "") -> dict[str, Any]:
    f = agents_md_path() if kind == "agents_md" else _skill_file(name, agent or None)
    try:
        text = f.read_text(encoding="utf-8")
    except FileNotFoundError:
        text = ""
    links = []
    if kind == "agents_md":
        target = agents_md_path()
        for a in adapters.load_all():
            for p in map(expand, a.instructions):
                if p.is_symlink() and Path(os.readlink(p)) == target:
                    links.append(contract(p))
    return {"kind": kind, "name": name, "agent": agent or None, "path": contract(f), "text": text,
            "version": version(f), "lines": len(text.splitlines()), "linkedFrom": links}


def save_text(body: dict[str, Any]) -> dict[str, Any]:
    kind, text = body.get("kind"), body.get("text")
    if kind not in ("agents_md", "skill"):
        raise BadRequest("kind must be agents_md or skill")
    if not isinstance(text, str) or len(text.encode("utf-8")) > MAX_TEXT:
        raise BadRequest("text must be at most 512 KB")
    front = re.match(r"^---\s*\n(.*?)\n---", text, re.S) if kind == "skill" else None
    if kind == "skill" and not (front and re.search(r"^name:\s*\S", front.group(1), re.M)):
        raise BadRequest("a SKILL.md has to start with front matter that has a name: (between --- lines)")
    f = agents_md_path() if kind == "agents_md" else _skill_file(str(body.get("name") or ""), body.get("agent"))
    backup = _Backup()
    _write(f, text if text.endswith("\n") else text + "\n", {contract(f): str(body.get("base") or "")}, backup)
    _log(f"{kind}.save", target=contract(f), backup=contract(backup.root))
    return {"saved": contract(f), "backup": contract(backup.root), **read_text(kind, str(body.get("name") or ""),
                                                                            str(body.get("agent") or ""))}


# ------------------------------------------------------------------ actions


def _target(agent: Any) -> mcpconf.Target:
    t = mcpconf.BY_AGENT.get(str(agent))
    if t is None or str(agent) not in _installed():
        raise BadRequest(f"{agent} isn't an installed agent with MCP support")
    return t


def _name(body: dict[str, Any]) -> str:
    name = str(body.get("name") or "")
    if not mcpconf.NAME_RE.match(name):
        raise BadRequest("server names use letters, digits, _ . @ and - (at most 64)")
    return name


def _set_servers(t: mcpconf.Target, change: dict[str, Any], versions: dict[str, str], backup: _Backup) -> None:
    try:
        text = mcpconf.updated_text(t, change)
    except mcpconf.ConfigError as e:
        raise BadRequest(str(e)) from None
    _write(t.file, text, versions, backup)


def _native_of(name: str, agent: str) -> tuple[dict[str, Any], bool]:
    """(native definition, parked?) of a server an agent has, switched on or off."""
    t = _target(agent)
    parked = overrides.load()["mcp_parked"].get(agent, {})
    if name in parked:
        return parked[name], True
    try:
        found = mcpconf.servers(t)
    except mcpconf.ConfigError as e:
        raise BadRequest(str(e)) from None
    if name not in found:
        raise BadRequest(f"{agent} has no MCP server {name}")
    return found[name], False


def mcp_add(body: dict[str, Any], versions: dict[str, str]) -> dict[str, Any]:
    name = _name(body)
    try:
        server = mcpconf.validate_neutral(body.get("server") or {})
    except mcpconf.ConfigError as e:
        raise BadRequest(str(e)) from None
    agents = [str(a) for a in body.get("agents") or []]
    if not agents:
        raise BadRequest("pick at least one agent")
    return _install(name, server, agents, versions, "mcp.add")


def mcp_copy(body: dict[str, Any], versions: dict[str, str]) -> dict[str, Any]:
    name = _name(body)
    source = str(body.get("source") or "")
    native, _ = _native_of(name, source)
    server = mcpconf.to_neutral(_target(source).dialect, native)
    agents = [str(a) for a in body.get("agents") or [] if a != source]
    if not agents:
        raise BadRequest("pick at least one agent to copy to")
    return _install(name, server, agents, versions, "mcp.copy", source=source)


def _install(name: str, server: dict[str, Any], agents: list[str], versions: dict[str, str], action: str,
             **extra: Any) -> dict[str, Any]:
    backup = _Backup()
    targets = [_target(a) for a in agents]
    for t in targets:  # check every file first, so nothing is half done
        _check(t.file, versions)
        if name in mcpconf.servers(t) or name in overrides.load()["mcp_parked"].get(t.agent, {}):
            raise BadRequest(f"{t.agent} already has an MCP server called {name}; remove it there first")
    for t in targets:
        _set_servers(t, {name: mcpconf.to_native(t.dialect, server)}, versions, backup)
    _log(action, target=name, agents=agents, backup=contract(backup.root), **extra)
    return {"done": f"{name} added to {', '.join(agents)}", "backup": contract(backup.root)}


def mcp_remove(body: dict[str, Any], versions: dict[str, str]) -> dict[str, Any]:
    name, agent = _name(body), str(body.get("agent") or "")
    t = _target(agent)
    _, parked = _native_of(name, agent)
    backup = _Backup()
    if parked:
        backup.save(overrides.path())
        ov = overrides.load()
        ov["mcp_parked"][agent].pop(name, None)
        overrides.save(ov)
    else:
        _set_servers(t, {name: None}, versions, backup)
    _log("mcp.remove", target=name, agent=agent, backup=contract(backup.root))
    return {"done": f"removed {name} from {agent}", "backup": contract(backup.root)}


def mcp_toggle(body: dict[str, Any], versions: dict[str, str]) -> dict[str, Any]:
    name, agent, on = _name(body), str(body.get("agent") or ""), bool(body.get("on"))
    t = _target(agent)
    native, parked = _native_of(name, agent)
    backup = _Backup()
    if t.dialect == "opencode" and not parked:
        _set_servers(t, {name: {**native, "enabled": on}}, versions, backup)
    elif on and parked:
        _set_servers(t, {name: native}, versions, backup)
        backup.save(overrides.path())
        ov = overrides.load()
        ov["mcp_parked"][agent].pop(name, None)
        overrides.save(ov)
    elif not on and not parked:
        _check(t.file, versions)
        try:
            text = mcpconf.updated_text(t, {name: None})
        except mcpconf.ConfigError as e:
            raise BadRequest(str(e)) from None
        backup.save(overrides.path())
        ov = overrides.load()
        ov["mcp_parked"].setdefault(agent, {})[name] = native
        overrides.save(ov)  # keep the definition before it leaves the agent's config
        try:
            _write(t.file, text, versions, backup)
        except Exception:
            ov["mcp_parked"][agent].pop(name, None)
            overrides.save(ov)
            raise
    _log("mcp.on" if on else "mcp.off", target=name, agent=agent, backup=contract(backup.root))
    return {"done": f"{name} switched {'on' if on else 'off'} for {agent}", "backup": contract(backup.root)}


def plugin_toggle(body: dict[str, Any], versions: dict[str, str], remove: bool = False) -> dict[str, Any]:
    import json

    agent, pid, on = str(body.get("agent") or ""), str(body.get("id") or ""), bool(body.get("on"))
    item = next((p for p in _plugins(_installed())["plugins"] if p["agent"] == agent and p["id"] == pid), None)
    if item is None:
        raise BadRequest(f"{agent} has no plugin {pid}")
    if item.get("locked"):
        raise BadRequest(f"{pid} is agent-guard's own plugin; it can't be switched off or removed here")
    if not item["canRemove" if remove else "canToggle"]:
        raise BadRequest(f"{'removing' if remove else 'switching'} {pid} isn't supported for {agent}")
    backup = _Backup()
    if agent == "codex":
        t = mcpconf.BY_AGENT["codex"]
        _, raw = mcpconf.read(t)
        try:
            text = mcpconf.toml_set_key(raw, ("plugins", pid), "enabled", on)
        except mcpconf.ConfigError as e:
            raise BadRequest(str(e)) from None
        _write(t.file, text, versions, backup)
    elif agent == "claude":
        f = expand("~/.claude/settings.json")
        data = json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
        plugins = data.setdefault("enabledPlugins", {})
        if remove:
            plugins.pop(pid, None)
        else:
            plugins[pid] = on
        _write(f, json.dumps(data, indent=2, ensure_ascii=False) + "\n", versions, backup)
    elif item["kind"] == "npm":
        t = mcpconf.BY_AGENT["opencode"]
        data, _ = mcpconf.read(t)
        data = dict(data or {})
        data["plugin"] = [p for p in data.get("plugin") or [] if str(p) != pid]
        _write(t.file, json.dumps(data, indent=2, ensure_ascii=False) + "\n", versions, backup)
    else:  # an OpenCode plugin file: switched off by renaming it to .off
        d = expand("~/.config/opencode/plugins")
        live, dead = d / pid, d / f"{pid}.off"
        src = live if live.exists() else dead
        backup.save(src)
        if remove:
            src.unlink()
        elif on and dead.exists():
            dead.rename(live)
        elif not on and live.exists():
            live.rename(dead)
    action = "plugin.remove" if remove else ("plugin.on" if on else "plugin.off")
    _log(action, target=pid, agent=agent, backup=contract(backup.root))
    verb = "removed" if remove else f"switched {'on' if on else 'off'}"
    return {"done": f"{pid} {verb} for {agent}", "backup": contract(backup.root)}


def _link_agents() -> list[adapters.Adapter]:
    return [a for a in _installed().values() if a.skills_mode == "link"]


def _unlink_skill(a: adapters.Adapter, name: str) -> None:
    for d in _skill_dirs(a):
        link = d / name
        if link.is_symlink():
            link.unlink()


def _link_skill(a: adapters.Adapter, name: str) -> None:
    target = skills.pool() / name
    for d in _skill_dirs(a):
        link = d / name
        if not (link.exists() or link.is_symlink()):
            d.mkdir(parents=True, exist_ok=True)
            os.symlink(target, link)


def skill_toggle(body: dict[str, Any]) -> dict[str, Any]:
    name, agent, on = str(body.get("name") or ""), body.get("agent"), bool(body.get("on"))
    if not SKILL_NAME.match(name):
        raise BadRequest("not a skill name")
    if name in LOCKED_SKILLS and not on:
        raise BadRequest(f"{name} is how agents learn the rules; it can't be switched off")
    ov = overrides.load()
    pool, parked_dir = skills.pool(), overrides.parked_skills_dir()
    own = skills.own_skills()
    backup = _Backup()
    backup.save(overrides.path())
    if agent:
        a = next((x for x in _link_agents() if x.name == agent), None)
        if a is None:
            raise BadRequest(f"{agent} reads the shared pool directly, so a skill can only be switched off for "
                             "every agent there" if agent in _installed() else f"{agent} isn't installed")
        off = ov["skills_off"].setdefault(a.name, [])
        if on:
            if name in off:
                off.remove(name)
            overrides.save(ov)
            if (pool / name / "SKILL.md").exists():
                _link_skill(a, name)
        else:
            if name not in off:
                off.append(name)
            overrides.save(ov)
            _unlink_skill(a, name)
        _log("skill.on" if on else "skill.off", target=name, agent=a.name)
        return {"done": f"{name} switched {'on' if on else 'off'} for {a.title}"}
    if on:
        if name not in ov["skills_parked"]:
            raise BadRequest(f"{name} isn't switched off")
        if name in own:
            if not ((pool / name).exists() or (pool / name).is_symlink()):
                pool.mkdir(parents=True, exist_ok=True)
                os.symlink(own[name], pool / name)
        elif (parked_dir / name).exists():
            shutil.move(str(parked_dir / name), str(pool / name))
        ov["skills_parked"].remove(name)
        overrides.save(ov)
        for a in _link_agents():
            if name not in skills.excluded(a):
                _link_skill(a, name)
    else:
        entry = pool / name
        if not (entry / "SKILL.md").exists():
            raise BadRequest(f"no shared skill {name}")
        backup.save(entry)
        if entry.is_symlink():
            entry.unlink()  # your own skill stays in the repo's skills/ folder
        else:
            parked_dir.mkdir(parents=True, exist_ok=True)
            shutil.move(str(entry), str(parked_dir / name))
        if name not in ov["skills_parked"]:
            ov["skills_parked"].append(name)
        overrides.save(ov)
        for a in _link_agents():
            _unlink_skill(a, name)
    _log("skill.on" if on else "skill.off", target=name, agent="all")
    return {"done": f"{name} switched {'on' if on else 'off'} for every agent", "backup": contract(backup.root)}


def skill_remove(body: dict[str, Any]) -> dict[str, Any]:
    name, agent = str(body.get("name") or ""), body.get("agent")
    if not SKILL_NAME.match(name):
        raise BadRequest("not a skill name")
    if name in LOCKED_SKILLS:
        raise BadRequest(f"{name} is how agents learn the rules; it can't be removed")
    backup = _Backup()
    if agent:  # an unshared copy in one agent's own folder
        src = _skill_file(name, str(agent)).parent
        backup.save(src)
        shutil.rmtree(src)
        _log("skill.remove", target=name, agent=str(agent), backup=contract(backup.root))
        return {"done": f"removed {name} from {agent}'s own skills", "backup": contract(backup.root)}
    pool, parked_dir, own = skills.pool(), overrides.parked_skills_dir(), skills.own_skills()
    found = False
    for p in (pool / name, parked_dir / name, own.get(name)):
        if p is not None and (p.exists() or p.is_symlink()):
            backup.save(p)
            if p.is_symlink() or p.is_file():
                p.unlink()
            else:
                shutil.rmtree(p)
            found = True
    if not found:
        raise BadRequest(f"no skill {name}")
    for a in _link_agents():
        _unlink_skill(a, name)
    backup.save(overrides.path())
    ov = overrides.load()
    ov["skills_parked"] = [s for s in ov["skills_parked"] if s != name]
    ov["skills_off"] = {k: [s for s in v if s != name] for k, v in ov["skills_off"].items()}
    overrides.save(ov)
    _log("skill.remove", target=name, agent="all", backup=contract(backup.root))
    note = " (it was one of your own skills in the repo's skills/ folder; commit or restore that there)" \
        if name in own else ""
    return {"done": f"removed {name} for every agent{note}", "backup": contract(backup.root)}


def skill_share(body: dict[str, Any]) -> dict[str, Any]:
    name, agent = str(body.get("name") or ""), str(body.get("agent") or "")
    src = _skill_file(name, agent).parent
    pool = skills.pool()
    if (pool / name).exists() or (pool / name).is_symlink():
        raise BadRequest(f"the shared pool already has a skill called {name}; remove or rename one of them first")
    backup = _Backup()
    backup.save(src)
    pool.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, pool / name, symlinks=True)
    shutil.rmtree(src)
    for a in _link_agents():
        if name not in skills.excluded(a):
            _link_skill(a, name)
    _log("skill.share", target=name, agent=agent, backup=contract(backup.root))
    return {"done": f"{name} is now shared with every agent", "backup": contract(backup.root)}


def manage(body: dict[str, Any]) -> dict[str, Any]:
    action = body.get("action")
    versions = body.get("versions") or {}
    if not isinstance(versions, dict):
        raise BadRequest("versions must be an object")
    handlers = {
        "mcp.add": lambda: mcp_add(body, versions),
        "mcp.copy": lambda: mcp_copy(body, versions),
        "mcp.remove": lambda: mcp_remove(body, versions),
        "mcp.toggle": lambda: mcp_toggle(body, versions),
        "plugin.toggle": lambda: plugin_toggle(body, versions),
        "plugin.remove": lambda: plugin_toggle(body, versions, remove=True),
        "skill.toggle": lambda: skill_toggle(body),
        "skill.remove": lambda: skill_remove(body),
        "skill.share": lambda: skill_share(body),
        "text.save": lambda: save_text(body),
    }
    if action not in handlers:
        raise BadRequest(f"unknown action {action!r}")
    with _LOCK:
        return handlers[action]()
