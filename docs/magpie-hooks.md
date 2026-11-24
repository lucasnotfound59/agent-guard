# Use agent-guard with Magpie Hooks

Magpie can write agent-guard's hook commands into Claude Code, Codex, Cursor and Gemini CLI. The agents run
the commands locally, and agent-guard applies the same warn-only rules and keeps the same activity log.
Start with a working [agent-guard install](../README.md#install). [Magpie Hooks documentation](https://usemagpie.ai/docs/library#hooks)

## Add the hooks

In Magpie, open **Library → Hooks**. Use **Add a hook** for a new entry or edit the existing entry for that row.

1. For **Name**, join `agent-guard`, the agent ID and the lowercase event name with hyphens. For example,
   use `agent-guard-claude-sessionstart`. Under **When it runs**, pick the agent-specific event, such as
   **Claude Code · SessionStart**, and select that agent. Use the table's matcher and leave the field empty
   where the table shows a dash.
2. For **Command**, use the full path to the installed guard binary, then the hook arguments. Use the agent ID
   in both `--agent` and `--format`, and set `--event` from the table. If the entry already exists in the agent's
   settings, copy the existing command exactly. This example prints the value to paste for Claude Code's first
   row on a default installation.

   ```bash
   printf '%s/bin/guard hook --agent claude --format claude --event session_start\n' \
     "${AGENT_GUARD_HOME:-$HOME/.agent-guard}"
   ```

3. Set **Timeout (seconds)** to **15** for Claude Code, Codex and Gemini CLI. Leave it empty for Cursor.
   Save the entry.

| Agent ID | When it runs | Matcher | `--event` |
| --- | --- | --- | --- |
| `claude` | `SessionStart` | — | `session_start` |
| `claude` | `PreToolUse` | `Bash` | `pre_command` |
| `claude` | `PostToolUse` | `Bash\|Edit\|Write\|MultiEdit\|NotebookEdit` | `auto` |
| `claude` | `Stop` | — | `stop` |
| `codex` | `SessionStart` | `startup\|resume` | `session_start` |
| `codex` | `PreToolUse` | `Bash` | `pre_command` |
| `codex` | `PostToolUse` | `Bash\|apply_patch\|Edit\|Write` | `auto` |
| `codex` | `Stop` | — | `stop` |
| `cursor` | `sessionStart` | — | `session_start` |
| `cursor` | `postToolUse` | — | `post_command` |
| `cursor` | `stop` | — | `stop` |
| `gemini` | `SessionStart` | — | `session_start` |
| `gemini` | `BeforeTool` | `run_shell_command` | `pre_command` |
| `gemini` | `AfterTool` | `run_shell_command\|write_file\|replace` | `auto` |
| `gemini` | `AfterAgent` | — | `stop` |

## Use one agent-guard hook per event

If `guard install` already wired an agent, Magpie keeps identical groups in place. That preserves their
position in Codex's hook list and keeps existing trust. Magpie tracks and removes groups it wrote itself.
[Magpie hook sync source](https://github.com/yetone/magpie/blob/v0.1.4730/internal/library/hooks.go)

Gemini CLI's direct installation names the command hook `agent-guard`. Magpie writes the same command without
the optional name, so both groups can appear. If you see both, back up `~/.gemini/settings.json`, check the
Magpie group's event and command, and remove the older named group for that event.

Check for one agent-guard group per event after rerunning `guard install` or syncing the Library. Before
running `guard uninstall`, remove the agent-guard entries from Magpie's Hooks tab.

## Verify

- Open a new session in each configured agent. In Codex, use `/hooks` to review and trust any new entries.
- Run `guard doctor --probe` to exercise session-start commands that match the installed agent-guard command.
- Run `guard report --since 1d` after a session to check that the agent recorded activity. If an entry is
  missing from an agent's settings, run `magpie library sync` and check the file again.
