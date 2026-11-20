"""Flagged secrets and personal details are never copied into the log, agent messages or the dashboard."""

import json
import os
import stat

from agent_guard import events, messages
from agent_guard.checks import check_files
from agent_guard.dashboard import data
from agent_guard.diff import from_text
from agent_guard.paths import log_file
from agent_guard.rules import Rule, global_rules

FAKE_KEY = "AKIA" "ABCDEFGHIJKLMNOP"  # the documented AWS example format, not a real key


def secret_finding():
    rule = next(r for r in global_rules("active") if r.id == "no-secrets")
    findings = check_files([rule], [from_text("app.py", f"AWS = '{FAKE_KEY}'\n")])
    assert findings, "no-secrets should flag the fake key"
    return findings[0]


def test_secret_lines_are_hidden_everywhere():
    f = secret_finding()
    assert FAKE_KEY not in f.snippet() and "line hidden" in f.snippet() and "app.py" in f.snippet()
    assert FAKE_KEY not in json.dumps(f.to_event())
    assert FAKE_KEY not in messages.for_agent([f]) and FAKE_KEY not in messages.for_terminal([f], "staged changes")
    assert f.key  # dedupe still works on the real line


def test_private_term_and_opt_in_rules_are_hidden_but_others_are_not():
    private = Rule(id="p", title="P", severity="hard", status="active", why="w", fix="f",
                   check={"kind": "added-line-forbidden", "pattern": "jane@corp", "private_terms": True})
    opt_in = Rule(id="o", title="O", severity="soft", status="active", why="w", fix="f",
                  check={"kind": "added-line-forbidden", "pattern": "token", "redact": "secret"})
    plain = Rule(id="n", title="N", severity="soft", status="active", why="w", fix="f",
                 check={"kind": "added-line-forbidden", "pattern": "TODO"})
    lines = from_text("a.md", "mail jane@corp\nmy token\n# TODO later\n")
    by_rule = {f.rule.id: f.snippet() for f in check_files([private, opt_in, plain], [lines])}
    assert "personal details" in by_rule["p"] and "jane@corp" not in by_rule["p"]
    assert "secret" in by_rule["o"]
    assert by_rule["n"] == "# TODO later"


def test_log_is_private_and_older_logs_are_tightened():
    events.log("finding", rule="x")
    assert stat.S_IMODE(log_file().stat().st_mode) == 0o600
    assert stat.S_IMODE(log_file().parent.stat().st_mode) == 0o700
    os.chmod(log_file(), 0o644)
    os.chmod(log_file().parent, 0o755)
    events.log("finding", rule="y")
    assert stat.S_IMODE(log_file().stat().st_mode) == 0o600
    assert stat.S_IMODE(log_file().parent.stat().st_mode) == 0o700


def test_logged_findings_no_longer_carry_the_secret():
    f = secret_finding()
    events.log("finding", agent="claude", kind="edit", **f.to_event())
    assert FAKE_KEY not in log_file().read_text()


def test_dashboard_hides_snippets_logged_by_older_versions():
    events.log("finding", agent="claude", rule="no-secrets", severity="hard", snippet=f"AWS = '{FAKE_KEY}'")
    events.log("finding", agent="claude", rule="no-ts-any", severity="soft", snippet="const x: any = 1")
    d = data.activity(data.LogCache(), "all", {}, 0, 10)
    snippets = {i["rule"]: i["snippet"] for i in d["items"]}
    assert FAKE_KEY not in snippets["no-secrets"] and "line hidden" in snippets["no-secrets"]
    assert snippets["no-ts-any"] == "const x: any = 1"
