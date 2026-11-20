"""The dashboard Toolbox: MCP formats, safe TOML edits, and every change action against a fake home."""

import json
import shutil
import stat
import tomllib

import pytest

from agent_guard import config, events, overrides, skills
from agent_guard.dashboard import data, mcpconf, toolbox
from agent_guard.paths import repo_root

REAL_REPO = repo_root()
SECRET = "sk-test-not-a-real-key-0000000000"


@pytest.fixture
def home(isolated, tmp_path, monkeypatch):
    """A copy of the regulation repo plus fake Claude, Codex and OpenCode configs in the fake home."""
    reg = tmp_path / "reg"
    for d in ("rules", "adapters", "global", "skills"):
        shutil.copytree(REAL_REPO / d, reg / d, symlinks=True)
    shutil.copy(REAL_REPO / "config.yaml", reg / "config.yaml")
    monkeypatch.setenv("AGENT_GUARD_REPO", str(reg))
    config.load.cache_clear()
    (isolated / ".claude/skills").mkdir(parents=True)
    (isolated / ".claude.json").write_text(json.dumps({"numStartups": 3, "mcpServers": {}}))
    (isolated / ".claude/settings.json").write_text(json.dumps({"hooks": {}, "enabledPlugins": {"tidy@market": True}}))
    (isolated / ".codex").mkdir()
    (isolated / ".codex/config.toml").write_text(
        '# my codex settings\nmodel = "gpt-x"\n\n[mcp_servers.rea]\ncommand = "rea"\nargs = ["mcp"]\n'
        'startup_timeout_sec = 20\n\n[mcp_servers.rea.env]\nREA_TOKEN = "' + SECRET + '"\n\n'
        '[plugins."github@curated"]\nenabled = true\n')
    (isolated / ".config/opencode/plugins").mkdir(parents=True)
    (isolated / ".config/opencode/opencode.json").write_text(json.dumps({"$schema": "x", "mcp": {}, "plugin": ["oc-x"]}))
    (isolated / ".config/opencode/plugins/agent-guard.js").write_text("// ours\n")
    (isolated / ".config/opencode/plugins/extra.js").write_text("// theirs\n")
    yield isolated
    config.load.cache_clear()


def versions():
    return toolbox.inventory()["versions"]


def do(**body):
    return toolbox.manage({**body, "versions": versions()})


# ------------------------------------------------------------------ formats


@pytest.mark.parametrize("dialect", ["claude", "codex", "gemini", "standard", "copilot", "opencode", "antigravity"])
def test_every_dialect_round_trips(dialect):
    stdio = {"transport": "stdio", "command": "npx", "args": ["-y", "pkg"], "env": {"K": "v"}, "cwd": "", "url": "",
             "headers": {}}
    http = {"transport": "http", "command": "", "args": [], "env": {}, "cwd": "", "url": "https://x.dev/mcp",
            "headers": {"Authorization": "Bearer t"}}
    for s in (stdio, http):
        assert mcpconf.same_server(mcpconf.to_neutral(dialect, mcpconf.to_native(dialect, s)), s), (dialect, s)


def test_toml_edits_keep_everything_else():
    raw = ('# keep me\nmodel = "m"\n\n[mcp_servers.a]\ncommand = "a"\n\n[mcp_servers.a.env]\nX = "1"\n\n'
           '[mcp_servers.b]\ncommand = "b"\n\n[other]\nkeep = true\n')
    out = mcpconf.toml_set_tables(raw, ("mcp_servers",), {"a": None, "my server": {"command": "c", "env": {"Y": "2"}}})
    parsed = tomllib.loads(out)
    assert set(parsed["mcp_servers"]) == {"b", "my server"}
    assert parsed["mcp_servers"]["my server"] == {"command": "c", "env": {"Y": "2"}}
    assert parsed["other"] == {"keep": True} and parsed["model"] == "m" and out.startswith("# keep me")


def test_toml_refuses_what_it_cannot_edit_safely():
    with pytest.raises(mcpconf.ConfigError):
        mcpconf.toml_set_tables('mcp_servers = { a = { command = "x" } }\n', ("mcp_servers",), {"a": None})
    with pytest.raises(mcpconf.ConfigError):
        mcpconf.toml_set_tables("not = [valid\n", ("mcp_servers",), {"a": None})


def test_secrets_never_reach_the_page(home):
    inv = toolbox.inventory()
    rea = next(s for s in inv["mcp"]["servers"] if s["name"] == "rea")
    assert rea["server"]["env"] == ["REA_TOKEN"]
    assert SECRET not in json.dumps(inv)
    assert toolbox._mask_args(["--api-key", "abc", "--token=xyz", "ghp_abc", "mcp-server-sequential-thinking"]) == \
        ["--api-key", "••••", "--token=••••", "••••", "mcp-server-sequential-thinking"]
    assert toolbox._mask_url("https://u:p@x.dev/mcp?api_key=1&v=2") == "https://••••@x.dev/mcp?api_key=••••&v=2"


# ------------------------------------------------------------------ MCP actions


def test_add_copy_toggle_and_remove_across_agents(home):
    server = {"transport": "stdio", "command": "npx", "args": ["-y", "fs"], "env": {"FS_KEY": SECRET}}
    do(action="mcp.add", name="fs", server=server, agents=["claude", "codex"])
    claude = json.loads((home / ".claude.json").read_text())
    assert claude["numStartups"] == 3  # the rest of the file is kept
    assert claude["mcpServers"]["fs"] == {"type": "stdio", "command": "npx", "args": ["-y", "fs"], "env": {"FS_KEY": SECRET}}
    codex = tomllib.loads((home / ".codex/config.toml").read_text())
    assert codex["mcp_servers"]["fs"]["env"] == {"FS_KEY": SECRET} and codex["mcp_servers"]["rea"]["startup_timeout_sec"] == 20
    assert (home / ".codex/config.toml").read_text().startswith("# my codex settings")

    do(action="mcp.copy", name="rea", source="codex", agents=["opencode"])
    oc = json.loads((home / ".config/opencode/opencode.json").read_text())["mcp"]["rea"]
    assert oc == {"type": "local", "command": ["rea", "mcp"], "enabled": True, "environment": {"REA_TOKEN": SECRET}}

    do(action="mcp.toggle", name="fs", agent="claude", on=False)
    assert "fs" not in json.loads((home / ".claude.json").read_text())["mcpServers"]
    assert overrides.load()["mcp_parked"]["claude"]["fs"]["command"] == "npx"
    assert stat.S_IMODE(overrides.path().stat().st_mode) == 0o600  # parked servers can hold keys
    row = next(s for s in toolbox.inventory()["mcp"]["servers"] if s["name"] == "fs")
    assert row["agents"] == {"claude": "off", "codex": "on"}
    do(action="mcp.toggle", name="fs", agent="claude", on=True)
    assert "fs" in json.loads((home / ".claude.json").read_text())["mcpServers"]
    assert "fs" not in overrides.load()["mcp_parked"].get("claude", {})

    do(action="mcp.toggle", name="rea", agent="opencode", on=False)  # OpenCode has its own enabled flag
    assert json.loads((home / ".config/opencode/opencode.json").read_text())["mcp"]["rea"]["enabled"] is False

    do(action="mcp.remove", name="rea", agent="codex")
    assert "rea" not in tomllib.loads((home / ".codex/config.toml").read_text())["mcp_servers"]
    logged = [r for r in events.read_log() if r["event"] == "toolbox"]
    assert [r["action"] for r in logged] == ["mcp.add", "mcp.copy", "mcp.off", "mcp.on", "mcp.off", "mcp.remove"]
    assert SECRET not in json.dumps(logged)
    assert list((home / ".agent-guard/backups").glob("*-toolbox-*/.codex/config.toml"))


def test_changes_refuse_a_file_that_changed_since_the_page_loaded(home):
    seen = versions()
    before = (home / ".claude.json").read_text()
    (home / ".claude.json").write_text(json.dumps({"numStartups": 4, "mcpServers": {}}))
    with pytest.raises(toolbox.Conflict):
        toolbox.manage({"action": "mcp.add", "name": "x", "agents": ["claude"], "versions": seen,
                        "server": {"transport": "http", "url": "https://x.dev/mcp"}})
    assert json.loads((home / ".claude.json").read_text())["numStartups"] == 4 and before != ""
    with pytest.raises(data.BadRequest):
        do(action="mcp.add", name="rea", agents=["codex"], server={"transport": "stdio", "command": "x"})  # exists
    with pytest.raises(data.BadRequest):
        do(action="mcp.add", name="bad name!", agents=["codex"], server={"transport": "stdio", "command": "x"})


# ------------------------------------------------------------------ skills


def test_skills_switch_per_agent_and_for_everyone(home):
    reg = repo_root()
    (reg / "skills/mine").mkdir()
    (reg / "skills/mine/SKILL.md").write_text("---\nname: mine\ndescription: Mine.\n---\nbody\n")
    shared = skills.pool() / "shared-one"
    shared.mkdir(parents=True)
    (shared / "SKILL.md").write_text("---\nname: shared-one\ndescription: >\n  Folded text.\n---\n")
    from agent_guard import adapters
    from agent_guard.state import InstallState

    skills.sync(adapters.load_all(), InstallState())
    link = home / ".claude/skills/shared-one"
    assert link.is_symlink()
    inv = {s["name"]: s for s in toolbox.inventory()["skills"]["skills"]}
    assert inv["shared-one"]["description"] == "Folded text." and inv["mine"]["kind"] == "own"

    toolbox.manage({"action": "skill.toggle", "name": "shared-one", "agent": "claude", "on": False})
    assert not link.exists() and overrides.skills_off("claude") == ["shared-one"]
    skills.sync(adapters.load_all(), InstallState())  # guard install respects it
    assert not link.exists()
    toolbox.manage({"action": "skill.toggle", "name": "shared-one", "agent": "claude", "on": True})
    assert link.is_symlink()

    for name in ("shared-one", "mine"):
        toolbox.manage({"action": "skill.toggle", "name": name, "on": False})
        assert not (skills.pool() / name).exists() and not (home / ".claude/skills" / name).exists()
    skills.sync(adapters.load_all(), InstallState())
    assert not (skills.pool() / "mine").exists()  # parked own skill isn't re-linked
    assert {s["name"] for s in toolbox.inventory()["skills"]["skills"] if s.get("parked")} == {"shared-one", "mine"}
    for name in ("shared-one", "mine"):
        toolbox.manage({"action": "skill.toggle", "name": name, "on": True})
        assert (skills.pool() / name / "SKILL.md").exists() and (home / ".claude/skills" / name).is_symlink()

    with pytest.raises(data.BadRequest):
        toolbox.manage({"action": "skill.toggle", "name": "agent-guard", "on": False})
    with pytest.raises(data.BadRequest):
        toolbox.manage({"action": "skill.remove", "name": "agent-guard"})
    with pytest.raises(data.BadRequest):
        toolbox.manage({"action": "skill.toggle", "name": "shared-one", "agent": "codex", "on": False})  # reads pool


def test_share_edit_and_remove_skills(home):
    stray = home / ".claude/skills/only-here"
    stray.mkdir(parents=True)
    (stray / "SKILL.md").write_text("---\nname: only-here\ndescription: x\n---\n")
    assert [u["name"] for u in toolbox.inventory()["skills"]["unshared"]] == ["only-here"]
    toolbox.manage({"action": "skill.share", "name": "only-here", "agent": "claude"})
    assert (skills.pool() / "only-here/SKILL.md").exists() and (home / ".claude/skills/only-here").is_symlink()

    t = toolbox.read_text("skill", "only-here")
    toolbox.manage({"action": "text.save", "kind": "skill", "name": "only-here", "text": t["text"] + "more\n",
                    "base": t["version"]})
    assert (skills.pool() / "only-here/SKILL.md").read_text().endswith("more\n")
    with pytest.raises(toolbox.Conflict):
        toolbox.manage({"action": "text.save", "kind": "skill", "name": "only-here", "text": t["text"],
                        "base": t["version"]})
    with pytest.raises(data.BadRequest):
        toolbox.manage({"action": "text.save", "kind": "skill", "name": "only-here", "text": "no front matter",
                        "base": toolbox.read_text("skill", "only-here")["version"]})
    with pytest.raises(data.BadRequest):
        toolbox.read_text("skill", "../../etc")

    toolbox.manage({"action": "skill.remove", "name": "only-here"})
    assert not (skills.pool() / "only-here").exists() and not (home / ".claude/skills/only-here").exists()
    assert list((home / ".agent-guard/backups").glob("*-toolbox-*/.agents/skills/only-here/SKILL.md"))


def test_agents_md_editor(home):
    t = toolbox.read_text("agents_md")
    assert t["text"].startswith("#") and t["lines"] > 10
    toolbox.manage({"action": "text.save", "kind": "agents_md", "text": t["text"] + "- One more line.\n",
                    "base": t["version"]})
    assert toolbox.agents_md_path().read_text().endswith("- One more line.\n")
    with pytest.raises(toolbox.Conflict):
        toolbox.manage({"action": "text.save", "kind": "agents_md", "text": "x\n", "base": t["version"]})


# ------------------------------------------------------------------ plugins


def test_plugins_toggle_and_remove(home):
    do(action="plugin.toggle", agent="codex", id="github@curated", on=False)
    assert tomllib.loads((home / ".codex/config.toml").read_text())["plugins"]["github@curated"]["enabled"] is False
    do(action="plugin.toggle", agent="claude", id="tidy@market", on=False)
    assert json.loads((home / ".claude/settings.json").read_text())["enabledPlugins"]["tidy@market"] is False
    do(action="plugin.remove", agent="claude", id="tidy@market")
    assert json.loads((home / ".claude/settings.json").read_text())["enabledPlugins"] == {}
    do(action="plugin.toggle", agent="opencode", id="extra.js", on=False)
    assert (home / ".config/opencode/plugins/extra.js.off").exists()
    do(action="plugin.remove", agent="opencode", id="oc-x")
    assert json.loads((home / ".config/opencode/opencode.json").read_text())["plugin"] == []
    with pytest.raises(data.BadRequest):
        do(action="plugin.toggle", agent="opencode", id="agent-guard.js", on=False)
    with pytest.raises(data.BadRequest):
        do(action="plugin.remove", agent="codex", id="github@curated")  # Codex plugins: on/off only


# ------------------------------------------------------------------ HTTP


def test_toolbox_routes(home):
    import threading

    from agent_guard.dashboard import server
    from tests.test_dashboard import call

    app = server.Dashboard(port=0)
    threading.Thread(target=app.serve_forever, daemon=True).start()
    try:
        assert call(app, "GET", "/api/toolbox", token=False)[0] == 401
        status, _, inv = call(app, "GET", "/api/toolbox")
        assert status == 200 and SECRET not in json.dumps(inv)
        status, _, md = call(app, "GET", "/api/text?kind=agents_md")
        assert status == 200 and md["text"]
        stale = dict(inv["versions"], **{"~/.codex/config.toml": "old"})
        status, _, body = call(app, "POST", "/api/manage", body={"action": "plugin.toggle", "agent": "codex",
                                                                 "id": "github@curated", "on": False, "versions": stale})
        assert status == 409 and body["error"]["code"] == "conflict"
        status, _, body = call(app, "POST", "/api/manage", body={"action": "nope", "versions": {}})
        assert status == 400
    finally:
        app.close()


def test_read_only_dashboard_refuses_changes(home):
    import threading

    from agent_guard.dashboard import server
    from tests.test_dashboard import call

    app = server.Dashboard(port=0, read_only=True)
    threading.Thread(target=app.serve_forever, daemon=True).start()
    try:
        assert call(app, "GET", "/api/version")[2]["readOnly"] is True
        before = (home / ".codex/config.toml").read_text()
        status, _, body = call(app, "POST", "/api/manage", body={"action": "plugin.toggle", "agent": "codex",
                                                                 "id": "github@curated", "on": False,
                                                                 "versions": toolbox.inventory()["versions"]})
        assert status == 403 and body["error"]["code"] == "read_only"
        assert (home / ".codex/config.toml").read_text() == before
        assert call(app, "POST", "/api/check", body={"mode": "command", "text": "ls"})[0] == 200  # checks only read
        assert call(app, "GET", "/api/toolbox")[0] == 200
    finally:
        app.close()
