# Changelog

## 1.0.0 — 2026-11-20

First complete version: one warn-only rulebook for every AI coding agent on this Mac.

**Coverage**
- Global git hooks (`core.hooksPath`) check every commit in every repo, by any agent or person: staged lines,
  gitleaks secrets, Conventional Commits, the repo's own `.agent-guard.yaml` checks, and FTC Knowledge Bank checks
  in integrated FTC repos. Each repo's own hooks still run afterwards, with their exit code.
- Agent hooks for Claude Code, Codex, Gemini CLI, Cursor, Copilot and OpenCode (OpenCode 2.x plugin API). They
  give the rule list at session start, warnings after edits and commands, and a summary of unresolved warnings
  at the end of a turn.
- One global `AGENTS.md` linked into every agent's instructions file, plus `~/.agents/AGENTS.md` for DSH and future
  agents. One shared skill pool, `~/.agents/skills`, read directly or linked into Claude, Cursor, Copilot,
  Antigravity, Qwen Code, Trae and Qoder.
- 15 active rules (9 hard, 6 soft) and 3 starter proposals.

**Learning loop**
- `guard propose` writes an inactive candidate. `guard review`, `guard approve` and `guard reject` refuse to run
  inside an agent or without a terminal.
- Approving or rejecting commits the change to this repo, including proposals that were never committed.
  Invalid rules can't be approved.

**Safety**
- Warn-only everywhere: nothing is blocked. A crash in agent-guard returns neutral hook output and is logged.
- `guard install` writes only what changed, and `--dry-run` shows what it would change. Before it replaces any
  real file or folder it saves a copy under `~/.agent-guard/backups/<run>/`, every time. Settings files it can't
  parse are left alone.
- `guard uninstall` restores what the first install changed.

**Visibility**
- Warnings reach the agent, you (agent UI where supported, plus debounced macOS notifications), and
  `~/.agent-guard/log/events.jsonl`.
- `guard report` ranks rules by agent and repo. `guard scan` lists existing patterns agents may copy.
- `guard doctor` checks wiring and rules, finds new agents with no adapter, and flags repos whose own
  `core.hooksPath` skips the global hooks. It also flags wired agents that have never sent a hook event, and
  OpenCode plugin load failures.
