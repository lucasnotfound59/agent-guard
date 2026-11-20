"""Each agent's documented hook payload -> our handling -> the agent's documented output shape."""

import json

import pytest

from agent_guard import hookio, hooks
from agent_guard.events import read_log

BAD_LINE = 'DATA = "/Users/someone/data.csv"\n'


def _write_bad(repo, name="app.py"):
    (repo / name).write_text(BAD_LINE)
    return str(repo / name)


def test_claude_post_edit_warns_agent_and_user_then_dedupes(repo):
    path = _write_bad(repo)
    payload = {"hook_event_name": "PostToolUse", "session_id": "s1", "cwd": str(repo), "tool_name": "Write",
               "tool_input": {"file_path": path, "content": BAD_LINE}}
    out = json.loads(hooks.run("claude", "claude", "auto", json.dumps(payload)))
    ctx = out["hookSpecificOutput"]
    assert ctx["hookEventName"] == "PostToolUse"
    assert "no-hardcoded-home-path" in ctx["additionalContext"] and "app.py:1" in ctx["additionalContext"]
    assert "no-hardcoded-home-path" in out["systemMessage"]
    assert hooks.run("claude", "claude", "auto", json.dumps(payload)) == ""  # same warning not repeated
    assert any(r["event"] == "finding" and r["agent"] == "claude" for r in read_log())


def test_claude_session_start_injects_rules(repo):
    payload = {"hook_event_name": "SessionStart", "session_id": "s2", "cwd": str(repo), "source": "startup"}
    out = json.loads(hooks.run("claude", "claude", "session_start", json.dumps(payload)))
    text = out["hookSpecificOutput"]["additionalContext"]
    assert "agent-guard is active" in text and "no-secrets" in text


def test_claude_pre_command_warns_user_never_denies(repo):
    payload = {"hook_event_name": "PreToolUse", "session_id": "s3", "cwd": str(repo), "tool_name": "Bash",
               "tool_input": {"command": "git push --force origin main"}}
    out = json.loads(hooks.run("claude", "claude", "auto", json.dumps(payload)))
    assert "no-destructive-git" in out["systemMessage"]
    assert "permissionDecision" not in json.dumps(out) and "decision" not in out


def test_stop_summarises_unresolved(repo):
    path = _write_bad(repo)
    edit = {"hook_event_name": "PostToolUse", "session_id": "s4", "cwd": str(repo), "tool_name": "Edit",
            "tool_input": {"file_path": path, "new_string": BAD_LINE}}
    hooks.run("claude", "claude", "auto", json.dumps(edit))
    stop = {"hook_event_name": "Stop", "session_id": "s4", "cwd": str(repo)}
    out = json.loads(hooks.run("claude", "claude", "auto", json.dumps(stop)))
    assert "still unresolved" in out["systemMessage"] and "decision" not in out


def test_codex_apply_patch(repo):
    _write_bad(repo, "lib.py")
    patch = "*** Begin Patch\n*** Add File: lib.py\n+" + BAD_LINE + "*** End Patch"
    payload = {"hook_event_name": "PostToolUse", "session_id": "c1", "cwd": str(repo), "tool_name": "apply_patch",
               "tool_input": {"command": patch}}
    out = json.loads(hooks.run("codex", "codex", "auto", json.dumps(payload)))
    assert "lib.py" in out["hookSpecificOutput"]["additionalContext"]


def test_gemini_after_tool_always_json(repo):
    path = _write_bad(repo)
    payload = {"hook_event_name": "AfterTool", "session_id": "g1", "cwd": str(repo), "tool_name": "write_file",
               "tool_input": {"file_path": path, "content": BAD_LINE}}
    out = json.loads(hooks.run("gemini", "gemini", "auto", json.dumps(payload)))
    assert out["hookSpecificOutput"]["hookEventName"] == "AfterTool"
    quiet = {"hook_event_name": "AfterTool", "session_id": "g1", "cwd": str(repo), "tool_name": "read_file",
             "tool_input": {"file_path": path}}
    assert json.loads(hooks.run("gemini", "gemini", "auto", json.dumps(quiet))) == {}


def test_cursor_post_tool_use(repo):
    path = _write_bad(repo)
    payload = {"hook_event_name": "postToolUse", "conversation_id": "k1", "cwd": str(repo), "tool_name": "Write",
               "tool_input": {"file_path": path}, "tool_output": "{}"}
    out = json.loads(hooks.run("cursor", "cursor", "post_command", json.dumps(payload)))
    assert "no-hardcoded-home-path" in out["additional_context"]
    stop = {"hook_event_name": "stop", "conversation_id": "k1", "status": "completed", "loop_count": 0}
    assert json.loads(hooks.run("cursor", "cursor", "stop", json.dumps(stop))) == {}  # no auto follow-up by default


def test_copilot_post_tool_use(repo):
    path = _write_bad(repo)
    payload = {"toolName": "edit", "toolArgs": json.dumps({"path": path}), "cwd": str(repo), "timestamp": 1}
    out = json.loads(hooks.run("copilot", "copilot", "post_command", json.dumps(payload)))
    assert "no-hardcoded-home-path" in out["additionalContext"]


def test_generic_for_opencode(repo):
    path = _write_bad(repo)
    payload = {"event": "post_edit", "cwd": str(repo), "session_id": "o1", "files": [path]}
    out = json.loads(hooks.run("opencode", "generic", "auto", json.dumps(payload)))
    assert "no-hardcoded-home-path" in out["agent"]


def test_post_command_checks_files_the_command_changed(repo):
    pre = {"hook_event_name": "PreToolUse", "session_id": "b1", "cwd": str(repo), "tool_name": "Bash",
           "tool_input": {"command": "python gen.py"}}
    hooks.run("claude", "claude", "auto", json.dumps(pre))
    _write_bad(repo, "generated.py")
    post = dict(pre, hook_event_name="PostToolUse")
    out = json.loads(hooks.run("claude", "claude", "auto", json.dumps(post)))
    assert "generated.py" in out["hookSpecificOutput"]["additionalContext"]


def test_outside_git_uses_edit_text(tmp_path):
    f = tmp_path / "loose.ts"
    f.write_text("let x: any = 1\n")
    payload = {"hook_event_name": "PostToolUse", "session_id": "n1", "cwd": str(tmp_path), "tool_name": "Write",
               "tool_input": {"file_path": str(f), "content": "let x: any = 1\n"}}
    out = json.loads(hooks.run("claude", "claude", "auto", json.dumps(payload)))
    assert "no-ts-any" in out["hookSpecificOutput"]["additionalContext"]


@pytest.mark.parametrize("fmt", hookio.FORMATS)
def test_garbage_input_never_breaks_the_agent(fmt):
    out = hooks.run("x", fmt, "auto", "{not json")
    assert out == hookio.get(fmt).neutral("auto")
    assert any(r["event"] == "hook_error" for r in read_log())


def _start(agent, fmt, repo, session, source):
    payload = {"hook_event_name": "SessionStart", "session_id": session, "cwd": str(repo), "source": source}
    out = json.loads(hooks.run(agent, fmt, "auto", json.dumps(payload)))
    return out["hookSpecificOutput"]["additionalContext"]


def test_session_start_text_is_framed_as_background(repo):
    text = _start("claude", "claude", repo, "bg1", "startup")
    assert "Hard:" in text and "not a request" in text and text.rstrip().endswith("latest request.")


def test_resume_gets_a_short_reminder_instead_of_the_full_list(repo):
    first = _start("codex", "codex", repo, "thread-1", "startup")
    again = _start("codex", "codex", repo, "thread-1", "resume")
    assert "Hard:" in first
    assert "Hard:" not in again and "still active" in again and "not a request" in again
    assert [r.get("source") for r in read_log() if r["event"] == "session_start"] == [None, "resume"]


def test_resume_repeats_the_rules_when_it_has_no_record_or_they_changed(repo):
    assert "Hard:" in _start("codex", "codex", repo, "never-started-here", "resume")
    _start("claude", "claude", repo, "s-change", "startup")
    (repo / ".agent-guard.yaml").write_text("disable:\n  - id: no-ts-any\n    reason: generated code\n")
    changed = _start("claude", "claude", repo, "s-change", "resume")
    assert "Hard:" in changed and "Disabled here: no-ts-any" in changed
    assert "Hard:" in _start("claude", "claude", repo, "s-change", "compact")  # context was summarised
