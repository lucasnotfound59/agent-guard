# AGENTS.md — working on the agent-guard repo itself

This repo is the machine-wide regulation for every AI agent on this computer. Changes here affect every
agent and every repository, so be careful and keep it small.

- `global/AGENTS.md` is symlinked into every agent's global instructions. Keep it short (under ~80 lines):
  every line costs context in every session of every agent.
- `rules/active/` is approved by the user only. Agents propose with `guard propose` (writes
  `rules/candidates/`); never edit `rules/active/` or `rules/retired/` directly.
- `adapters/<agent>.yaml` says where an agent keeps instructions, skills and hooks. Hook dialects live in
  `src/agent_guard/hookio/<format>.py`; the writers that edit agent configs live in `src/agent_guard/adapters.py`.
- Hooks must never break an agent: every hook path catches exceptions and returns the format's neutral output.
  Warn-only: never return a deny/block decision.
- Run tests with the runtime venv: `~/.agent-guard/venv/bin/python -m pytest -q` (from the repo root).
  Tests redirect HOME, the state dir and git's global config into temp dirs; they never touch the real ones.
- After changing adapters, hook writers or skills: `guard install` then `guard doctor`.
- Commit messages: Conventional Commits (`feat`, `fix`, `patch`, `docs`, `refactor`, `test`, `chore`, `rebase`, ...).
