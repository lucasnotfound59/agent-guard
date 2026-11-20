# Changelog

## Unreleased

**Dashboard**
- `guard dashboard`: a local web view of the event log, rules, proposals and health, ported from FTC
  Knowledge Bank's rule dashboard (loopback only, one-time token, Host/Origin checks, live updates, light and dark).
  It can dry-run proposals and test rules on a repo, diff, command or commit message. Approving stays in
  `guard review`.
- Dashboard Toolbox: every agent's MCP servers, skills and plugins in one place, plus the global AGENTS.md. Add an
  MCP server to several agents at once, copy one between agents (format converted), switch servers, skills and
  plugins on or off per agent, remove them, share single-agent skills, and edit SKILL.md and AGENTS.md. Each change
  is backed up, refused if the file changed meanwhile, and logged; secret values never reach the page.
  `guard install` respects skills switched off there (`~/.agent-guard/overrides.json`).
- `guard report --json` and `guard report --since all`.

**Fixes**
- Commits in other projects' test-suite repos (`pytest-of-*` temp folders) no longer reach the log or notifications.
  They were most of all logged warnings. Configurable with `ignore_repos`.
- Repo discovery (`guard doctor`, the proposal dry-run) stops at `node_modules`, `.worktrees` and each `.git`, and a
  scan that hits its 30 s timeout keeps what it found instead of silently returning no repos. Doctor says when
  that happens.
- Hooks for parallel tool calls no longer lose or corrupt each other's session state (merged under a lock and
  written atomically), so warnings aren't repeated.
- One unreadable rule file (bad YAML, not a mapping) no longer breaks every hook, `guard doctor` and `guard review`;
  it is skipped and reported as invalid.
- The log keeps 5 rotated files instead of growing forever, and `guard report` and doctor read the rotated ones too.

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
