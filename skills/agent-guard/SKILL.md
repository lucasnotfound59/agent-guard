---
name: agent-guard
description: Use when an "agent-guard" warning appears after an edit, command or commit; when the user corrects a general habit of yours (propose it as a rule so every agent learns it); or before you finish or commit work on this machine (run guard check). Covers reading warnings, fixing or justifying them, and proposing rules for the user to approve.
---

# agent-guard

agent-guard is the machine-wide rulebook for every AI agent on this computer. Hooks in your harness and the
global git hooks check what you add against shared rules and warn both you and the user. It is warn-only:
nothing is blocked, but every warning is logged, and the user sees which agent caused it.

## When a warning arrives

Each warning names a rule, a location, why it matters, and the fix:

```
agent-guard (warn-only) found 1 issue(s) in your last change:
- [hard] no-hardcoded-home-path at src/app.py:12: `open("/Users/someone/data.csv")`
  why: The path breaks on every other machine ...  fix: Use a path relative to the project ...
```

1. Fix it in the same turn. Hard rules are things that are almost always wrong; soft rules are strong defaults.
2. If the line is right on purpose, keep it and say why on the line:
   `data = load(raw)  # agent-guard: allow no-ts-any third-party callback has no types`
   Then tell the user what you kept and why.
3. Never "fix" a warning by bypassing it: no `--no-verify`, `HUSKY=0`, `core.hooksPath` changes, editing
   hook configs, or editing the rules.

## Commands

| Command | Use |
| --- | --- |
| `guard rules` | Rules that apply in the current repo (global + the repo's `.agent-guard.yaml`) |
| `guard check` | Check all uncommitted changes. Run it before saying you are done. `--staged` checks what a commit would contain |
| `guard check --command "git push -f"` | Ask whether a command breaks a rule before running it |
| `guard check --message "feat: add x"` | Check a commit message |
| `guard scan .` | Existing bad patterns in this repo (don't copy them) |
| `guard propose ...` | Propose a new rule (below) |

## Propose a rule when the user corrects a habit

When the user says something like "don't do X again" or "always do Y" and it applies beyond this one file,
turn it into a rule so every agent on the machine learns it:

```
guard propose --id no-print-debugging --title "No print() debugging left in code" \
  --pattern '^\s*print\(' --applies-to '*.py' --exclude 'scripts/**' \
  --severity soft --why "Debug prints leak into output and logs" \
  --fix "Use the logging module, or remove the print" \
  --evidence "User: stop leaving print statements everywhere" --agent claude
```

- Kinds (`--kind`): `added-line-forbidden` (default; regex on new lines), `added-line-required`,
  `path-forbidden` (use `--paths` globs), `command-forbidden` (regex on shell commands), `commit-message`,
  `diff-size`.
- Make the pattern specific enough to avoid false alarms; use `--applies-to` / `--exclude` globs.
- `--agent` is your own name (claude, codex, gemini, cursor, opencode, copilot, dsh, ...).
- The proposal is saved but not active. Tell the user, who approves it with `guard review` in a terminal.
  You cannot approve it yourself.
- If the habit can't be expressed as a pattern (it's about judgement), tell the user instead; they may add
  it to `global/AGENTS.md` in the agent-guard repo.

## Repo-specific overrides

A repo can add `.agent-guard.yaml` at its root with extra `rules:`, `exclude_paths:`, project `checks:` that
run at commit time, and `disable:` entries (each needs an `id` and a `reason`). Only add or change this file
when the user asks.
