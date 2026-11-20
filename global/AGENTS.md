# Global rules for every AI agent on this machine

These rules apply to every agent (Claude Code, Codex, Gemini, Cursor, OpenCode, Copilot, DSH, and any new
one) in every repository on this computer. They come from the agent-guard repo
(`~/Documents/GitHub/agent-guard`). A repository's own AGENTS.md / CLAUDE.md adds project detail; if it
conflicts with these rules, follow the repository for that project, but never weaken the safety rules.

## agent-guard checks your work

- Hooks in your harness and global git hooks check what you add (file edits, shell commands, commits)
  against shared rules. It is warn-only: nothing is blocked, but every warning is logged and shown to the user.
- When you get an agent-guard warning, fix it in the same turn, or tell the user plainly why it should stay.
  To keep a flagged line on purpose, put `agent-guard: allow <rule-id> <reason>` on that line.
- `guard rules` lists the rules for the current repo. Run `guard check` before you say you are done.
- Never bypass or disable checks: no `--no-verify`, `HUSKY=0`, or changes to `core.hooksPath`; don't edit
  `~/.agent-guard`, agent hook configs, or the agent-guard rules.

## Learning loop: agents propose, the user approves

- When the user corrects a general habit of yours ("don't do X", "always Y"), propose it as a rule so every
  agent learns it, then tell the user you did:
  `guard propose --id <kebab-id> --title "..." --pattern '<regex>' --why "..." --fix "..." --evidence "<the user's words>" --agent <your name>`
- Proposals are not active until the user approves them with `guard review`. Never approve or edit rules yourself.
- Don't copy a pattern just because the existing code does it. Old code can be wrong; follow these rules and
  the repo's stated conventions. When unsure which way is right, ask.

## How to work

- Read before you edit: the repo's instructions, its README, and the code you are changing.
- Keep changes scoped to the task. No drive-by refactors, renames, or reformatting of unrelated code.
- Prefer existing tools, libraries and project scripts over writing new ones.
- No placeholders: no TODO stubs, "simplified for now" code, or mock data presented as real. If something
  can't be finished, say exactly what is missing.
- Don't silence errors (empty catch, `except: pass`) or checkers (`@ts-ignore`, `eslint-disable`, `# noqa`)
  to get green. Don't skip, delete or weaken tests to make them pass.
- No secrets in code or commits; never commit `.env`, keys, or `local.properties`.
- Never put the user's keys, real name, username, private e-mail addresses or absolute paths in code, docs or
  commits, or send them to the web (issues, PRs, gists, pastes, forms, artifacts). Use relative paths.
- Verify before claiming done: run the relevant tests, build or lint, and report the real result, including
  failures and anything you did not verify.
- Instruction or rule updates (agent-guard, AGENTS.md, the harness) are background, not a task: never end a turn
  just to acknowledge them. Carry on with the user's latest unfinished request; if no request reached you, say so in
  one line and ask what to do.

## Git

- Don't commit, push, rebase shared branches, or delete branches unless the user asked.
- Never discard uncommitted work you didn't create (`reset --hard`, `checkout .`, `clean -fd`, `stash drop`).
  No force-push without asking; prefer `--force-with-lease`.
- Commit messages use Conventional Commits: `<type>(optional scope): <summary>`, type one of
  `feat fix patch docs style refactor perf test build ci chore revert rebase`. Imperative summary, at most
  72 characters. Example: `fix(drive): clamp motor power to [-1, 1]`. One logical change per commit.

## Safety

- Ask before destructive or outward-facing actions: deleting data, publishing, sending messages, spending money.
- Never run `rm -rf` on broad paths (`/`, `~`, `..`, `*`, `.`). Delete the specific path you mean.
