"""Hardening found while building the dashboard: rotated logs, concurrent hook state, unreadable rule files."""

import json
import shutil

import pytest

from agent_guard import cli, config, doctor, events, report
from agent_guard.paths import log_file, repo_root
from agent_guard.rules import active_rules, global_rules, validate

REAL_REPO = repo_root()


@pytest.fixture
def reg(tmp_path, monkeypatch):
    r = tmp_path / "reg"
    shutil.copytree(REAL_REPO / "rules", r / "rules")
    shutil.copy(REAL_REPO / "config.yaml", r / "config.yaml")
    monkeypatch.setenv("AGENT_GUARD_REPO", str(r))
    config.load.cache_clear()
    yield r
    config.load.cache_clear()


# ------------------------------------------------------------------ the log


def test_read_log_includes_rotated_logs_in_order(monkeypatch):
    events.log("finding", rule="first")
    monkeypatch.setattr(events, "MAX_LOG_BYTES", 1)  # the next write rotates the live log away
    events.log("finding", rule="second")
    monkeypatch.setattr(events, "MAX_LOG_BYTES", 20_000_000)
    events.log("finding", rule="third")
    assert len(events.log_files()) == 2
    assert [r["rule"] for r in events.read_log()] == ["first", "second", "third"]
    assert [r["rule"] for r in events.read_log(since_ts=0)] == ["first", "second", "third"]


def test_rotation_keeps_only_the_newest_copies(monkeypatch):
    d = log_file().parent
    d.mkdir(parents=True)
    for stamp in range(1000, 1007):
        (d / f"events.{stamp}.jsonl").write_text("")
    (d / "events.notes.jsonl").write_text("not ours")
    log_file().write_text('{"ts": "2026-01-01T00:00:00+00:00", "event": "x"}\n')
    monkeypatch.setattr(events, "MAX_LOG_BYTES", 1)
    events.log("finding", rule="r")
    rotated = sorted(p.name for p in d.glob("events.*.jsonl") if p.name.split(".")[1].isdigit())
    assert len(rotated) == events.MAX_ROTATED_LOGS
    assert "events.1000.jsonl" not in rotated and (d / "events.notes.jsonl").exists()


def test_non_object_lines_are_skipped():
    log_file().parent.mkdir(parents=True)
    log_file().write_text('[1, 2]\n"text"\n{"ts": "2026-11-20T00:00:00+00:00", "event": "ok"}\n')
    assert [r["event"] for r in events.read_log(since_ts=0)] == ["ok"]


# ------------------------------------------------------------------ session state


def test_parallel_hooks_keep_each_others_marks():
    a = events.SessionState("claude", "s1")
    b = events.SessionState("claude", "s1")  # loaded before a saves, like two parallel tool calls
    a.mark("k1")
    a.touch("/w/app", "a.ts")
    a.save()
    b.mark("k2")
    b.touch("/w/app", "b.ts")
    b.save()
    fresh = events.SessionState("claude", "s1")
    assert {"k1", "k2"} <= set(fresh.data["seen"])
    assert fresh.touched("/w/app") == ["a.ts", "b.ts"]


def test_corrupt_state_starts_fresh():
    s = events.SessionState("claude", "s2")
    s.save()
    s.path.write_text('{"seen": ')  # a half-written file from an old version
    fresh = events.SessionState("claude", "s2")
    assert fresh.data["seen"] == [] and fresh.is_new("anything")
    s.path.write_text('["not", "a", "dict"]')
    assert events.SessionState("claude", "s2").data["touched"] == {}


# ------------------------------------------------------------------ rule files


def test_one_unreadable_rule_file_does_not_disable_the_others(reg, repo):
    (reg / "rules/active/broken.yaml").write_text("id: broken\ncheck: [unclosed\n")
    (reg / "rules/candidates/listy.yaml").write_text("- just\n- a list\n")
    rules, _ = active_rules(repo)
    ids = {r.id for r in rules}
    assert "broken" not in ids and "no-secrets" in ids
    broken = next(r for r in global_rules("active") if r.id == "broken")
    assert validate(broken) and "cannot read broken.yaml" in validate(broken)[0]
    assert "not a rule" in validate(next(r for r in global_rules("candidate") if r.id == "listy"))[0]
    assert any(lvl == "fail" and "broken" in msg for lvl, msg in doctor.results())
    assert cli.main(["rules", "--candidates"]) == 0


# ------------------------------------------------------------------ report


def test_report_json_and_all_time(capsys):
    events.log("finding", agent="codex", rule="no-ts-any", severity="soft", repo="/w/app", kind="edit")
    assert report.parse_since("all") is None
    assert cli.main(["report", "--since", "all", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["since"] == "all" and out["warnings"] == 1 and out["by_repo"][0]["label"] == "app"
    assert "all time" in report.report("all")


# ------------------------------------------------------------------ repo discovery and test-suite repos


def test_commits_in_test_suite_repos_are_left_alone(isolated, repo, monkeypatch):
    from agent_guard import install
    from tests.conftest import git

    install.install()
    monkeypatch.delenv("AGENT_GUARD_CHECK_TEST_REPOS")  # behave like the real machine
    assert "pytest-of-" in str(repo)  # pytest's tmp_path, like any other project's test suite
    (repo / "app.py").write_text('DATA = "/Users/someone/x.csv"\n')
    git(repo, "add", "app.py")
    r = git(repo, "commit", "-m", "base", check=False)
    assert r.returncode == 0 and "agent-guard" not in r.stderr
    assert not any(e["event"] == "finding" for e in events.read_log())
    assert config.ignored_repo(repo) and not config.ignored_repo(REAL_REPO)


def test_scan_repos_prunes_and_keeps_partial_results_on_timeout(isolated, tmp_path, monkeypatch):
    import subprocess

    base = isolated / "Documents"
    for rel in ("a", "b/c", "web/node_modules/dep", "a/.worktrees/x"):
        (base / rel / ".git").mkdir(parents=True)
    config.load.cache_clear()
    repos, timed_out = config.scan_repos()
    assert [p.relative_to(base).as_posix() for p in repos] == ["a", "b/c"] and timed_out == []

    def slow(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], 30, output=f"{base}/a/.git\n{base}/b/c/.gi")

    monkeypatch.setattr(config.subprocess, "run", slow)
    repos, timed_out = config.scan_repos()
    assert [p.name for p in repos] == ["a"] and timed_out == ["~/Documents"]
