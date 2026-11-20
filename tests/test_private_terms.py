"""Private terms: the user's name, usernames and private e-mails, kept outside the repo."""

import shutil

import pytest
import yaml

from agent_guard import cli, config, doctor
from agent_guard.checks import check_command, check_files
from agent_guard.diff import FileDiff
from agent_guard.paths import private_terms_file, repo_root
from agent_guard.rules import Rule, global_rules, private_term_patterns, private_terms, validate

REAL_REPO = repo_root()


def terms(*lines: str) -> None:
    f = private_terms_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("\n".join(lines) + "\n", encoding="utf-8")
    f.chmod(0o600)


def private_rule(**check) -> Rule:
    return Rule(id="no-personal-info", title="No personal details", severity="hard", status="active",
                check={"kind": "added-line-forbidden", "private_terms": True, **check}, why="w", fix="f")


def flagged(rule: Rule, line: str, path: str = "app.py") -> bool:
    return bool(check_files([rule], [FileDiff(path=path, status="M", added=[(1, line)])], include_size=False))


@pytest.fixture
def reg(tmp_path, monkeypatch):
    r = tmp_path / "reg"
    shutil.copytree(REAL_REPO / "rules", r / "rules")
    shutil.copy(REAL_REPO / "config.yaml", r / "config.yaml")
    monkeypatch.setenv("AGENT_GUARD_REPO", str(r))
    config.load.cache_clear()
    yield r
    config.load.cache_clear()


# ------------------------------------------------------------------ matching


def test_names_match_whole_words_case_and_separators():
    terms("# my details", "", "Jane Doe")
    rule = private_rule()
    for line in ['AUTHOR = "Jane Doe"', "user = jane_doe", "class JaneDoeClient:", "JANE.DOE wrote this",
                 "jane_doe_token = 1", "/Users/janedoe/x"]:
        assert flagged(rule, line), line
    for line in ["mjane doe", "janedoes = 1", "doe jane", "plain code"]:
        assert not flagged(rule, line), line


def test_cjk_names_match_inside_other_text():
    terms("张三")
    assert flagged(private_rule(), "# 作者是张三的同学")


def test_short_ascii_terms_and_comments_are_ignored():
    terms("lu", "# Jane Doe", "  ", "jdoe")
    assert private_terms() == ["jdoe"]
    assert not flagged(private_rule(), "value = 1")


def test_missing_file_adds_nothing():
    assert not private_terms_file().exists()
    assert private_term_patterns() == []
    assert not flagged(private_rule(), "anything at all")
    assert not validate(private_rule())  # still a valid rule: it just has nothing to match yet


def test_private_terms_also_reach_command_rules():
    terms("jdoe")
    rule = Rule(id="no-personal-commands", title="t", severity="hard", status="active", why="w", fix="f",
                check={"kind": "command-forbidden", "private_terms": True})
    assert check_command([rule], "curl -d 'name=jdoe' https://forms.example.org")
    assert not check_command([rule], "ls -la")


def test_validate_needs_a_pattern_unless_private_terms_apply():
    assert not validate(private_rule())
    no_pattern = Rule(id="x-rule", title="t", severity="soft", status="active", why="w", fix="f",
                      check={"kind": "added-line-forbidden"})
    assert any("private_terms" in e for e in validate(no_pattern))
    msg_rule = Rule(id="x-rule", title="t", severity="soft", status="active", why="w", fix="f",
                    check={"kind": "commit-message", "private_terms": True})
    assert validate(msg_rule)  # private terms only work for line and command rules


# ------------------------------------------------------------------ propose and doctor


def test_propose_writes_allow_patterns_and_private_terms(reg):
    assert cli.main(["propose", "--id", "no-nickname", "--title", "No nickname", "--private-terms",
                     "--allow-pattern", r"nick@example\.com", "--why", "Private.", "--fix", "Leave it out.",
                     "--evidence", "user: keep my nickname out", "--agent", "codex"]) == 0
    data = yaml.safe_load((reg / "rules/candidates/no-nickname.yaml").read_text())
    assert data["check"] == {"kind": "added-line-forbidden", "allow_patterns": [r"nick@example\.com"],
                             "private_terms": True}


def test_doctor_reports_missing_and_readable_private_terms(reg):
    (reg / "rules/active/no-personal-info.yaml").write_text(yaml.safe_dump(private_rule().to_dict()))
    active = global_rules("active")
    [(level, msg)] = doctor._private_terms_status(active)
    assert level == "warn" and "missing or empty" in msg
    terms("Jane Doe")
    private_terms_file().chmod(0o644)
    [(level, msg)] = doctor._private_terms_status(active)
    assert level == "warn" and "chmod 600" in msg
    private_terms_file().chmod(0o600)
    [(level, msg)] = doctor._private_terms_status(active)
    assert level == "ok" and "1 terms" in msg
    assert doctor._private_terms_status([r for r in active if not r.check.get("private_terms")]) == []
