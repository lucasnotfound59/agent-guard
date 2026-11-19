# agent-guard

One rulebook for every AI coding agent on this machine: Claude Code, Codex, Gemini CLI, Antigravity, Cursor,
OpenCode, Copilot, DSH, and whatever comes next.

AI agents copy what they see. If a repo has `any` types, swallowed errors, or `/Users/me/` paths, the next agent
writes more of them. agent-guard gives every agent the same instructions and skills, checks what each one adds
(edits, shell commands, commits) against shared rules, and warns both the agent and you. When you correct an
agent, it can turn the correction into a rule proposal, and you approve it once for every agent.

**Warn-only.** Nothing is ever blocked. Warnings go to the agent (so it fixes them in the same turn), to you
(in the agent's window and as a macOS notification), and to a log you can review with `guard report`.

## What it sets up

| Layer | Where | Covers |
| --- | --- | --- |
| Global instructions | `global/AGENTS.md`, symlinked to `~/.claude/CLAUDE.md`, `~/.codex/AGENTS.md`, `~/.gemini/GEMINI.md`, `~/.config/opencode/AGENTS.md`, `~/.copilot/copilot-instructions.md`, `~/.agents/AGENTS.md` | every agent that reads a global instructions file (Cursor gets it injected by its session-start hook) |
| Shared skills | `~/.agents/skills` pool; your own skills live in `skills/` here | Codex, Gemini, OpenCode and DSH read the pool directly; Claude, Cursor, Copilot and Antigravity get symlinks |
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

`guard install` is idempotent. Re-run it after adding an adapter or a skill. Everything it replaces is backed
up first under `~/.agent-guard/backups/`, and `guard uninstall` puts it all back.

## Daily use

| Command | What it does |
| --- | --- |
| `guard report --since 7d` | Which rules each agent breaks most, across all repos. This is how you spot bad habits |
| `guard review` | Approve, edit or reject rules that agents proposed (needs a terminal, so agents can't approve their own) |
| `guard scan <repo>` | Bad patterns already in a repo that agents will copy |
| `guard rules` | Rules that apply in the current repo |
| `guard check [--staged]` | Check uncommitted changes now |
| `guard doctor` | Health check, including agents installed since the last install |

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
- **Per-repo overlay:** `.agent-guard.yaml` at a repo root can add `rules:`, `exclude_paths:`, commit-time
  `checks:`, and `disable:` entries. Each disable needs an `id` and a `reason`.
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
src/agent_guard/     engine (Python, PyYAML only); hookio/ has one module per hook dialect
git-hooks/           the global git hook dispatcher
templates/           OpenCode plugin
tests/               pytest; tests never touch your real home, state or git config
```

Limits: agents without hook support (Antigravity, DSH until its experimental hooks plugin is enabled) are only
checked at commit time. A repo with its own local `core.hooksPath` (for example husky) bypasses the global git
hooks. Pattern rules catch text, not intent; judgement calls belong in `global/AGENTS.md`.
