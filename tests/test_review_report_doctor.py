"""The learning loop (propose -> approve/reject), the machine-wide report and scan, and doctor's repo checks."""

import shutil
from pathlib import Path

import pytest
import yaml

from agent_guard import cli, config, doctor, events, report, review
from agent_guard.paths import repo_root
from agent_guard.rules import global_rules
from tests.conftest import git

REAL_REPO = repo_root()


@pytest.fixture
def reg(tmp_path, monkeypatch):
    """A throwaway regulation repo holding a committed copy of the real rules."""
    r = tmp_path / "reg"
    shutil.copytree(REAL_REPO / "rules", r / "rules")
    shutil.copy(REAL_REPO / "config.yaml", r / "config.yaml")
    monkeypatch.setenv("AGENT_GUARD_REPO", str(r))
    config.load.cache_clear()
    git(r, "init", "-q")
    git(r, "add", ".")
    git(r, "commit", "-q", "-m", "chore: seed rules")
    yield r
    config.load.cache_clear()


def propose(**over):
    args = dict(id="no-print-debug", title="No print debugging", kind="added-line-forbidden",
                patterns=[r"\bprint\("], severity="soft", why="Noise in logs.", fix="Use the logger.",
                evidence="user: stop leaving print() calls", agent="codex", applies_to=["*.py"])
    args.update(over)
    return review.propose(**args)


def last_subject(r: Path) -> str:
    return git(r, "log", "-1", "--format=%s").stdout.strip()


def test_propose_writes_an_inactive_candidate(reg):
    ok, msg = propose()
    assert ok and "NOT active" in msg
    cand = reg / "rules/candidates/no-print-debug.yaml"
    data = yaml.safe_load(cand.read_text())
    assert data["status"] == "candidate" and data["source"]["proposed_by"] == "codex"
    assert "no-print-debug" not in {r.id for r in global_rules("active")}


@pytest.mark.parametrize("over,why", [
    ({"id": "no-secrets"}, "already exists"),
    ({"id": "../escape"}, "kebab-case"),
    ({"patterns": ["("]}, "bad regex"),
    ({"kind": "made-up"}, "check.kind"),
])
def test_propose_refuses_bad_input(reg, over, why):
    ok, msg = propose(**over)
    assert not ok and why in msg
    assert not (reg / "rules/candidates/no-print-debug.yaml").exists()


def test_approve_commits_a_proposal_that_was_never_committed(reg):
    propose()  # an agent's proposal is an untracked file
    msg = review.approve("no-print-debug")
    assert "not committed" not in msg, msg
    active = yaml.safe_load((reg / "rules/active/no-print-debug.yaml").read_text())
    assert active["status"] == "active" and active["source"]["approved_by"] == "user"
    assert not (reg / "rules/candidates/no-print-debug.yaml").exists()
    assert last_subject(reg) == "feat(rules): add no-print-debug"
    assert git(reg, "status", "--porcelain").stdout == ""


def test_approve_a_committed_candidate(reg):
    propose()
    git(reg, "add", ".")
    git(reg, "commit", "-q", "-m", "chore(rules): propose no-print-debug")
    assert "not committed" not in review.approve("no-print-debug")
    assert git(reg, "status", "--porcelain").stdout == ""
    assert "rules/candidates/no-print-debug.yaml" not in git(reg, "ls-files").stdout


def test_approve_refuses_an_invalid_candidate(reg):
    propose()
    cand = reg / "rules/candidates/no-print-debug.yaml"
    data = yaml.safe_load(cand.read_text())
    data["check"]["pattern"] = "("  # e.g. broken while editing it during review
    cand.write_text(yaml.safe_dump(data))
    msg = review.approve("no-print-debug")
    assert msg.startswith("not approved") and "bad regex" in msg
    assert cand.exists() and not (reg / "rules/active/no-print-debug.yaml").exists()


def test_reject_keeps_the_rule_in_retired_and_commits(reg):
    propose()
    msg = review.reject("no-print-debug", "too noisy for scripts")
    assert "not committed" not in msg, msg
    retired = yaml.safe_load((reg / "rules/retired/no-print-debug.yaml").read_text())
    assert retired["status"] == "rejected" and retired["source"]["rejected_reason"] == "too noisy for scripts"
    assert last_subject(reg) == "chore(rules): reject no-print-debug"


def test_only_the_user_can_approve(reg, monkeypatch, capsys):
    propose()
    assert "interactive terminal" in review.require_user()  # pytest's stdin is not a terminal
    monkeypatch.setenv("CLAUDECODE", "1")
    assert "inside an agent (claude)" in review.require_user()
    assert cli.main(["approve", "no-print-debug"]) == 2
    assert cli.main(["reject", "no-print-debug", "--reason", "x"]) == 2
    assert cli.main(["review"]) == 2
    assert (reg / "rules/candidates/no-print-debug.yaml").exists()


def test_report_ranks_rules_and_agent_habits(capsys):
    for agent, rule in [("codex", "no-ts-any"), ("codex", "no-ts-any"), ("claude", "no-secrets")]:
        events.log("finding", agent=agent, rule=rule, repo="/w/app", kind="edit")
    events.log("suppressed", agent="codex", rule="no-ts-any", repo="/w/app")
    out = report.report("1d")
    assert "3 warning(s)" in out
    assert "2  no-ts-any" in out and "2  codex · no-ts-any" in out and "app" in out
    assert "1 line(s) kept on purpose" in out
    assert cli.main(["report", "--since", "7x"]) == 2
    assert "use e.g." in capsys.readouterr().err


def test_scan_reports_existing_patterns(repo):
    assert "clean" in report.scan(str(repo))
    (repo / "load.py").write_text('DATA = "/Users/someone/data.csv"\n')
    git(repo, "add", "load.py")
    git(repo, "commit", "-q", "-m", "feat: load data")
    out = report.scan(str(repo))
    assert "no-hardcoded-home-path" in out and "load.py" in out


def test_doctor_flags_repos_that_replace_the_global_hooks(repo, monkeypatch):
    monkeypatch.setattr(config, "load", lambda: {**config.DEFAULTS, "scan_roots": [str(repo.parent)]})
    assert doctor.local_hook_overrides() == []
    git(repo, "config", "core.hooksPath", ".husky")
    found = doctor.local_hook_overrides()
    assert [(p.resolve(), hp) for p, hp in found] == [(repo.resolve(), ".husky")]
    warns = [m for lvl, m in doctor.results() if lvl == "warn" and "own core.hooksPath" in m]
    assert warns and "git pre-commit" in warns[0]


def test_doctor_reads_opencode_plugin_load_errors(isolated):
    plugin = isolated / ".config/opencode/plugins/agent-guard.js"
    log = isolated / ".local/share/opencode/log/opencode.log"
    log.parent.mkdir(parents=True)
    log.write_text(f'level=INFO msg="loading plugin" id={plugin}\n'
                   f'level=WARN message="failed to load plugin" target={plugin} cause="Plugin must export a default"\n')
    assert "must export a default" in doctor.opencode_plugin_error(plugin)
    with log.open("a") as fh:
        fh.write(f'level=INFO msg="loading plugin" id={plugin}\n')  # a later load went through
    assert doctor.opencode_plugin_error(plugin) is None


def test_doctor_flags_wired_agents_that_never_sent_an_event(isolated):
    from agent_guard import install
    from tests.test_install_and_git import seed

    seed(isolated)
    install.install()
    def quiet() -> set[str]:
        return {m.split(":")[0] for _lvl, m in doctor.results() if "no hook events" in m}

    assert "Claude Code" in quiet()
    events.log("session_start", agent="claude")
    assert "Claude Code" not in quiet()
    assert not any("no hook events" in m for m in doctor.quick_issues())  # shown in doctor, not notified daily
