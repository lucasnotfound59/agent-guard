# agent-guard

One rulebook for every AI coding agent on this machine: Claude Code, Codex, Gemini CLI, Antigravity, Cursor,
OpenCode, Copilot, DSH, Qwen Code, Trae, Qoder, and whatever comes next.

AI agents copy what they see. If a repo has `any` types, swallowed errors, or `/Users/me/` paths, the next agent
writes more of them. agent-guard gives every agent the same instructions and skills, checks what each one adds
(edits, shell commands, commits) against shared rules, and warns both the agent and you. When you correct an
agent, it can turn the correction into a rule proposal, and you approve it once for every agent.

**Warn-only.** Nothing is ever blocked. Warnings go to the agent (so it fixes them in the same turn), to you
(in the agent's window and as a macOS notification), and to a log you can review with `guard report`.

## What it sets up

| Layer | Where | Covers |
| --- | --- | --- |
| Global instructions | `global/AGENTS.md`, symlinked to `~/.claude/CLAUDE.md`, `~/.codex/AGENTS.md`, `~/.gemini/GEMINI.md`, `~/.config/opencode/AGENTS.md`, `~/.copilot/copilot-instructions.md`, `~/.qwen/QWEN.md`, `~/.agents/AGENTS.md` | every agent that reads a global instructions file (Cursor gets it injected by its session-start hook; Trae and Qoder keep user rules in their app settings, so paste it there) |
| Shared skills | `~/.agents/skills` pool; your own skills live in `skills/` here | Codex, Gemini, OpenCode and DSH read the pool directly; Claude, Cursor, Copilot, Antigravity, Qwen Code, Trae and Qoder get symlinks |
| Agent hooks | each agent's user-level hook config (`adapters/*.yaml`) | warnings right after an edit or command, the rule list at session start, an unresolved-warnings summary at the end of a turn |
| Global git hooks | `git config --global core.hooksPath ~/.agent-guard/git-hooks` | every commit in every repo, by any agent or person; the repo's own hooks still run afterwards |
| Secret scanning | [gitleaks](https://github.com/gitleaks/gitleaks) at commit time, built-in patterns on every edit | keys, tokens, private keys |
| FTC repos | runs FTC-Knowledge-Bank's `project.py check` at commit time when a repo is integrated | FTC rule checks become automatic instead of optional |

## Install

```bash
./install.sh          # private Python runtime (uv), gitleaks (Homebrew), then `guard install`
guard doctor          # every agent wired? any new agent without an adapter?
```

Codex runs new hooks only after you trust them: open Codex, run `/hooks`, and trust the agent-guard entries.

`guard install` is idempotent: it only writes files that need to change, and `guard install --dry-run` shows what
it would do. Re-run it after adding an adapter or a skill. Before it replaces any real file or folder it saves a
copy under `~/.agent-guard/backups/<run>/`, every time. That includes files that appeared after the first install,
such as an editor saving over a linked `CLAUDE.md`. Settings files it can't parse (for example JSON with comments)
are left alone and reported.

`guard uninstall` puts back everything the first install changed and removes agent-guard's hooks from settings
you edited since. `guard uninstall --purge` also deletes the runtime, logs and backups.

## Daily use

| Command | What it does |
| --- | --- |
| `guard dashboard` | Local web dashboard: warnings over time, habits, rules and what each caught, the full event log, proposals with a dry-run, a rule tester, doctor, and a Toolbox for every agent's skills, MCP servers, plugins and AGENTS.md |
| `guard report --since 7d` | Which rules each agent breaks most, across all repos (`--since all`, `--json` for scripts). This is how you spot bad habits |
| `guard review` | Approve, edit or reject rules that agents proposed (needs a terminal, so agents can't approve their own) |
| `guard scan <repo>` | Bad patterns already in a repo that agents will copy |
| `guard rules` | Rules that apply in the current repo |
| `guard check [--staged]` | Check uncommitted changes now |
| `guard doctor` | Health check: every agent wired, rules valid, agents installed since the last install, and repos whose own `core.hooksPath` skips the global git hooks |

## Dashboard

`guard dashboard` serves a page on `127.0.0.1` (any free port, or `--port`) and opens it (`--no-browser` just
prints the link). It reads the event log, the rule files, git and `guard doctor`. Only the Toolbox changes files:

- **Overview**: warnings per hour/day, top rules, agents, repos, and agent × rule habits, with alerts for hook
  errors, invalid rule files and waiting proposals.
- **Rules**: every active, proposed and retired rule with its check, what it caught in the window, and an optional
  repo overlay (what that repo's `.agent-guard.yaml` disables, excludes and adds).
- **Activity**: the whole log with filters and search. **Check**: run the rules on a repo's uncommitted or staged
  changes, a diff, a command or a commit message, like `guard check`. **Health**: doctor, plus when each agent last
  sent a hook event.
- **Proposals**: the evidence, a dry-run against your repos, and the commands to approve or reject. Approving stays
  in your terminal (`guard review`), so an agent with a browser can't approve its own rule.
- **Toolbox**: every installed agent's MCP servers (Claude Code, Codex, Gemini, Cursor, Copilot, Qwen Code, OpenCode,
  Antigravity), skills and plugins side by side. Add an MCP server once and install it into the agents you pick,
  copy one to agents that lack it (converted to each agent's format), switch servers, skills and plugins on or off
  per agent, remove them, share a skill only one agent has, and edit SKILL.md files and the global AGENTS.md.
  Every change backs up the file under `~/.agent-guard/backups/`, is refused if the file changed since the page
  loaded it, and shows in Activity. Secret values (env, headers, token-like arguments) never reach the page.
  Switched-off servers and skills are kept in `~/.agent-guard/overrides.json` (readable only by you), which
  `guard install` respects. agent-guard's own skill and OpenCode plugin can't be switched off or removed here.

The link carries a fresh token in its `#fragment`; the page keeps it in memory only. The server answers only
requests with that token, the exact `127.0.0.1:<port>` Host and no foreign Origin, and changes need its own
Origin. Anyone holding the link can use the Toolbox, including an agent that started the dashboard, so keep the
link to yourself. Ported from FTC Knowledge Bank's rule dashboard.

## Rules

One YAML file per rule in `rules/active/`. Rules only look at **added** lines, so old code never nags, but no
agent can add more of it.

```yaml
id: no-hardcoded-home-path
title: No absolute home-directory paths in code
severity: hard            # hard = almost always wrong; soft = strong default (both only warn today)
applies_to: ['**']
exclude: ['*.md']
check:
  kind: added-line-forbidden   # also: added-line-required, path-forbidden, command-forbidden, commit-message, diff-size
  pattern: '(?<![\w.-])/(?:Users|home)/[A-Za-z0-9._-]+/'
why: The path breaks on every other machine and leaks your username.
fix: Use a path relative to the project, an environment variable, or Path.home().
```

- **Keep a flagged line on purpose:** add `agent-guard: allow <rule-id> <reason>` on that line. It's logged as kept, with the reason.
- **Hidden lines:** warnings from `no-secrets`, from rules that use private terms, and from rules with
  `check.redact: true` (or `redact: secret`) name the file and line but never repeat the line itself: not in the log,
  not in agent messages, not in the dashboard. The log (`~/.agent-guard/log/`) is readable only by you.
- **Per-repo overlay:** `.agent-guard.yaml` at a repo root can add `rules:`, `exclude_paths:`, commit-time
  `checks:`, and `disable:` entries. Each disable needs an `id` and a `reason`.
- **Private terms:** a rule with `check.private_terms: true` also matches every line of
  `~/.agent-guard/private-terms.txt`: your real name, usernames and private e-mail addresses, one per line (`#` for
  comments). The file is never committed, so rules can catch personal details without publishing them. Create it
  yourself and `chmod 600` it; `guard doctor` warns when a rule needs it and it is missing or readable by others.
  Names match case-insensitively, across separators and as whole words (`Jane Doe` also catches `jane_doe`,
  `JaneDoeClient` and `/Users/janedoe/`, but not `janedoes`).
- **Learning loop:** agents run `guard propose ...` when you correct a general habit. That writes
  `rules/candidates/<id>.yaml`, which is not active. `guard review` shows how often the rule would have matched in
  your repos, and approving it commits `feat(rules): add <id>` here.

## New agents

`guard doctor` reports agents it recognises that have no adapter yet. To wire one up, copy
`adapters/_template.yaml.example` to `adapters/<agent>.yaml` and run `guard install`. Most new agents copy Claude
Code's hook format (`writer: claude-settings`). Agents without hooks are still covered by `~/.agents/AGENTS.md`,
`~/.agents/skills`, and the global git hooks.

## Layout

```
global/AGENTS.md     the one global instruction file
rules/               active/ (approved), candidates/ (proposed), retired/
adapters/            one YAML per agent: instructions, skills, hooks
skills/              your own skills, shared to every agent
src/agent_guard/     engine (Python, PyYAML only); hookio/ has one module per hook dialect; dashboard/ is the web UI
git-hooks/           the global git hook dispatcher
templates/           OpenCode plugin
tests/               pytest; tests never touch your real home, state or git config
```

Limits:
- Agents without hook support (Antigravity, Qwen Code, Trae, Qoder, and DSH until its experimental
  `@deepseek-ai/dsh-hooks-claude-code` plugin is installed) get the rules and skills but are only checked at
  commit time.
- Commits in repos matching `ignore_repos` in `config.yaml` (by default pytest's `pytest-of-*` temp folders, where
  other projects' test suites commit) are not checked, logged or notified.
- A repo with its own local `core.hooksPath` (for example husky) skips the global git hooks. `guard doctor` lists
  these repos and the two lines to add to their hooks.
- Command rules match the command's text, so a command that only mentions a pattern (for example as an argument
  to `guard check --command`) can still warn.
- Pattern rules catch text, not intent; judgement calls belong in `global/AGENTS.md`.
