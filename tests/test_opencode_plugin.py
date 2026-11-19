"""Load the generated OpenCode plugin in Node with a fake OpenCode context and a stub `guard`, and check it
registers the hook, sends the right payloads, and appends warnings to the tool result."""

import json
import shutil
import subprocess

import pytest

from agent_guard.paths import repo_root

NODE = shutil.which("node")

SCRIPT = r"""
const mod = await import(process.argv[1]);
const plugin = mod.default;
const hooks = {};
const sessionDir = process.argv[2];
await plugin.setup({
  tool: { hook: async (name, fn) => { hooks[name] = fn; } },
  session: { get: async ({ sessionID }) => ({ id: sessionID, location: { directory: sessionDir } }) },
});
const write = { tool: "write", sessionID: "s1", status: "completed", input: { path: "src/app.ts", content: "x: any" },
                result: { output: "Created file successfully: app.ts" } };
await hooks["execute.after"](write);
const shell = { tool: "shell", sessionID: "s1", status: "completed", input: { command: "ls", workdir: "/tmp" },
                result: { content: [{ type: "text", text: "a b" }] } };
await hooks["execute.after"](shell);
const failed = { tool: "write", status: "error", input: { path: "x" }, error: { message: "denied" } };
await hooks["execute.after"](failed);
const other = { tool: "read", status: "completed", input: { path: "x" }, result: { output: "text" } };
await hooks["execute.after"](other);
console.log(JSON.stringify({ id: plugin.id, hooks: Object.keys(hooks), write, shell, failed, other }));
"""


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_plugin_registers_and_appends_warnings(tmp_path):
    calls = tmp_path / "calls.jsonl"
    stub = tmp_path / "guard"
    stub.write_text(f"#!/bin/sh\ncat >> '{calls}'\necho >> '{calls}'\n"
                    "echo '{\"agent\": \"agent-guard (warn-only) found 1 issue(s)\", \"user\": \"\"}'\n")
    stub.chmod(0o755)
    tpl = (repo_root() / "templates/opencode-plugin.js").read_text()
    plugin = tmp_path / "agent-guard.mjs"
    plugin.write_text(tpl.replace("__GUARD_BIN__", str(stub)).replace("__AGENT__", "opencode"))

    project = tmp_path / "project"  # the session's directory; the server itself runs somewhere else
    project.mkdir()
    r = subprocess.run([NODE, "--input-type=module", "-e", SCRIPT, str(plugin), str(project)], capture_output=True,
                       text=True, cwd=tmp_path, timeout=60)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["id"] == "agent-guard" and out["hooks"] == ["execute.after"]

    texts = [c["text"] for c in out["write"]["result"]["content"]]
    assert texts == ["Created file successfully: app.ts", "agent-guard (warn-only) found 1 issue(s)"]
    assert [c["text"] for c in out["shell"]["result"]["content"]][-1].startswith("agent-guard")
    assert "result" not in out["failed"] and out["other"]["result"] == {"output": "text"}

    sent = [json.loads(line) for line in calls.read_text().splitlines() if line.strip()]
    assert len(sent) == 2  # failed calls and non-edit tools aren't checked
    edit, cmd = sent
    assert edit["event"] == "post_edit" and edit["files"] == [str(project / "src/app.ts")]
    assert edit["edits"] == {str(project / "src/app.ts"): "x: any"} and edit["session_id"] == "s1"
    assert cmd == {"session_id": "s1", "event": "post_command", "command": "ls", "cwd": "/tmp"}
