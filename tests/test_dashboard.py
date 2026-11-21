"""`guard dashboard`: the HTTP guard rails (token, Host, Origin, methods, bodies) and the read-only payloads."""

import http.client
import json
import shutil
import threading

import pytest

from agent_guard import config, events
from agent_guard.dashboard import data, server
from agent_guard.paths import repo_root
from tests.conftest import git

REAL_REPO = repo_root()


@pytest.fixture
def reg(tmp_path, monkeypatch):
    """A throwaway regulation repo with a copy of the real rules, so tests can add files to it."""
    r = tmp_path / "reg"
    shutil.copytree(REAL_REPO / "rules", r / "rules")
    shutil.copy(REAL_REPO / "config.yaml", r / "config.yaml")
    monkeypatch.setenv("AGENT_GUARD_REPO", str(r))
    config.load.cache_clear()
    yield r
    config.load.cache_clear()


@pytest.fixture
def dash(reg):
    app = server.Dashboard(port=0)
    t = threading.Thread(target=app.serve_forever, daemon=True)
    t.start()
    yield app
    app.close()


def call(app, method, path, *, token=True, headers=None, body=None):
    conn = http.client.HTTPConnection("127.0.0.1", app.port, timeout=10)
    h = {"Host": f"127.0.0.1:{app.port}"}
    if token:
        h["Authorization"] = f"Bearer {app.token}"
    if body is not None and not isinstance(body, bytes):
        body = json.dumps(body).encode()
        h["Content-Type"] = "application/json"
        h["Origin"] = app.origin
    h.update(headers or {})
    h = {k: v for k, v in h.items() if v is not None}  # None removes a default header
    conn.request(method, path, body=body, headers=h)
    resp = conn.getresponse()
    raw = resp.read()
    conn.close()
    payload = json.loads(raw) if resp.getheader("Content-Type", "").startswith("application/json") else raw
    return resp.status, resp, payload


# ------------------------------------------------------------------ guard rails


def test_page_is_served_with_a_strict_csp(dash):
    status, resp, body = call(dash, "GET", "/", token=False)
    assert status == 200 and b"agent-guard dashboard" in body
    assert "script-src 'self'" in resp.getheader("Content-Security-Policy")
    assert resp.getheader("Cache-Control") == "no-store"
    assert call(dash, "GET", "/app.js", token=False)[0] == 200
    assert call(dash, "GET", "/styles.css", token=False)[0] == 200


@pytest.mark.parametrize("path", ["/../rules/active/no-secrets.yaml", "/static/app.js", "/data.py", "/%2e%2e/config.yaml"])
def test_only_the_three_page_files_are_served(dash, path):
    assert call(dash, "GET", path, token=False)[0] == 404


def test_api_needs_the_exact_token(dash):
    status, _, body = call(dash, "GET", "/api/overview", token=False)
    assert status == 401 and body["error"]["code"] == "unauthorized"
    status, _, _ = call(dash, "GET", "/api/overview", token=False, headers={"Authorization": "Bearer nope"})
    assert status == 401
    status, _, body = call(dash, "GET", "/api/overview")
    assert status == 200 and body["ok"] and body["apiVersion"] == 1 and body["version"]


def test_foreign_hosts_and_origins_are_refused(dash):
    for headers, code in [
        ({"Host": f"localhost:{dash.port}"}, "forbidden_host"),  # DNS rebinding: another name for 127.0.0.1
        ({"Origin": "http://evil.example"}, "forbidden_origin"),
        ({"Sec-Fetch-Site": "cross-site"}, "forbidden_origin"),
    ]:
        status, _, body = call(dash, "GET", "/api/overview", headers=headers)
        assert status == 403 and body["error"]["code"] == code, headers


def test_methods_queries_and_bodies_are_strict(dash):
    assert call(dash, "GET", "/api/check")[0] == 405
    assert call(dash, "DELETE", "/api/overview")[0] == 405
    assert call(dash, "GET", "/api/nope")[0] == 404
    assert call(dash, "GET", "/api/overview?window=7d&window=24h")[0] == 400
    status, _, body = call(dash, "GET", "/api/overview?window=forever")
    assert status == 400 and "window" in body["error"]["message"]
    # POST: same Origin and JSON only
    no_origin = call(dash, "POST", "/api/check", body={"mode": "command", "text": "ls"}, headers={"Origin": None})
    assert no_origin[0] == 403 and "POST requires" in no_origin[2]["error"]["message"]
    plain = call(dash, "POST", "/api/check", body=b"{}", headers={"Origin": dash.origin, "Content-Type": "text/plain"})
    assert plain[0] == 415
    not_object = call(dash, "POST", "/api/check", body=b"[1]", headers={"Origin": dash.origin, "Content-Type": "application/json"})
    assert not_object[0] == 400


def test_version_reports_pending_proposals_and_changes_with_the_log(dash, reg):
    _, _, first = call(dash, "GET", "/api/version")
    assert first["pending"] == len(list((reg / "rules/candidates").glob("*.yaml")))
    events.log("finding", agent="codex", rule="no-ts-any", severity="soft", repo="/w/app", kind="edit")
    _, _, second = call(dash, "GET", "/api/version")
    assert second["version"] != first["version"]


def test_check_endpoint_runs_rules_without_logging(dash):
    status, _, body = call(dash, "POST", "/api/check", body={"mode": "command", "text": "git push --force origin main"})
    assert status == 200
    assert [f["rule"] for f in body["findings"]] == ["no-destructive-git"]
    assert body["findings"][0]["why"] and body["findings"][0]["fix"]
    assert not any(r.get("event") == "finding" for r in events.read_log())


# ------------------------------------------------------------------ payloads


def seed():
    events.log("session_start", agent="codex", repo="/w/app", rules=15)
    for rule, sev in [("no-ts-any", "soft"), ("no-ts-any", "soft"), ("no-secrets", "hard")]:
        events.log("finding", agent="codex", rule=rule, severity=sev, repo="/w/app", kind="edit", path="a.ts", line=3,
                   snippet="const x: any = 1")
    events.log("finding", agent="claude", rule="no-ts-any", severity="soft", repo="/w/web", kind="commit")
    events.log("suppressed", agent="codex", rule="no-ts-any", severity="soft", repo="/w/app", suppressed="legacy API")
    events.log("hook_error", agent="cursor", error="KeyError: 'x'")


def test_overview_counts_and_series(reg):
    seed()
    d = data.overview(data.LogCache(), "24h")
    s = d["summary"]
    assert (s["warnings"], s["hard"], s["soft"], s["kept"], s["sessions"], s["errors"]) == (4, 1, 3, 1, 1, 1)
    assert s["by_rule"][0] == {"key": "no-ts-any", "count": 3, "severity": "soft"}
    assert s["habits"][0] == {"agent": "codex", "rule": "no-ts-any", "count": 2}
    assert len(d["series"]["rows"]) == 24 and sum(r["hard"] + r["soft"] for r in d["series"]["rows"]) == 4
    assert d["recent"][0]["event"] == "hook_error"  # newest first


def test_activity_filters_searches_and_pages(reg):
    seed()
    log = data.LogCache()
    d = data.activity(log, "all", {"event": "finding", "agent": "codex"}, 0, 2)
    assert d["total"] == 3 and len(d["items"]) == 2
    assert data.activity(log, "all", {"event": "finding", "agent": "codex"}, 2, 2)["items"][0]["rule"] == "no-ts-any"
    assert data.activity(log, "all", {"q": "legacy api"}, 0, 50)["total"] == 1
    facets = {f["key"]: f["count"] for f in d["facets"]["agent"]}
    assert facets["codex"] == 5
    with pytest.raises(data.BadRequest):
        data.activity(log, "all", {}, 0, 5000)


def test_rules_carry_window_stats_and_a_repo_overlay(reg, repo):
    seed()
    (repo / ".agent-guard.yaml").write_text("disable:\n  - id: no-ts-any\n    reason: generated client\n")
    d = data.rules(data.LogCache(), "all", str(repo))
    by_id = {r["id"]: r for r in d["rules"]}
    assert by_id["no-ts-any"]["stats"]["hits"] == 3 and by_id["no-ts-any"]["stats"]["kept"] == 1
    assert by_id["no-ts-any"]["here"] == "disabled" and by_id["no-secrets"]["here"] == "on"
    assert d["overlay"]["disabled"] == [{"id": "no-ts-any", "reason": "generated client"}]
    assert {r["status"] for r in d["rules"]} >= {"active", "candidate"}
    with pytest.raises(data.BadRequest):
        data.rules(data.LogCache(), "all", "relative/path")


def test_proposals_list_candidates_with_terminal_commands(reg):
    d = data.proposals(data.LogCache(), "all")
    ids = {c["id"] for c in d["candidates"]}
    assert ids == {p.stem for p in (reg / "rules/candidates").glob("*.yaml")}
    first = d["candidates"][0]
    assert first["commands"]["approve"] == f"guard approve {first['id']}"


def test_check_working_tree_uses_the_repo(reg, repo):
    (repo / "app.ts").write_text("export const f = (x: any) => x;\n")
    git(repo, "add", "app.ts")
    d = data.check({"mode": "staged", "repo": str(repo)})
    assert d["files"] == 1 and [f["rule"] for f in d["findings"]] == ["no-ts-any"]
    with pytest.raises(data.BadRequest):
        data.check({"mode": "working"})  # no repo picked
    with pytest.raises(data.BadRequest):
        data.check({"mode": "made-up"})


def test_dry_run_refuses_unknown_rules(reg):
    with pytest.raises(data.BadRequest):
        data.dry_run("does-not-exist")


def test_checked_records_count_but_stay_out_of_latest_events(reg):
    events.log("finding", agent="codex", rule="no-ts-any", severity="soft", repo="/w/app", kind="edit")
    for _ in range(15):
        events.log("checked", agent="codex", files=1, commands=1, warnings=0)
    d = data.overview(data.LogCache(), "24h")
    assert d["summary"]["checked_files"] == 15 and d["summary"]["checked_commands"] == 15
    assert [r["event"] for r in d["recent"]] == ["finding"]
    rows = {r["name"]: r for r in data.health(data.LogCache(), data.HealthCache(), "24h")["agents"]}
    assert rows["codex"]["checked"] == 30
