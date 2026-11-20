---
name: deepseek-delegate
description: Delegate the current user-authorized Git coding task to local DeepSeek Harness V4.1 Flash at max effort, then require a separate native Codex GPT subagent to review its actual diff. Use only when the user explicitly invokes $deepseek-delegate or asks to delegate this task to DSH.
---

# DeepSeek delegate

Use this skill only for the current task. Its presence does not authorize automatic delegation of future projects. DSH sends the task prompt and any files it reads to the configured DeepSeek provider, so keep the scope to what the user requested and exclude unrelated private data.

The local controller is `~/Documents/LLM/codex-dsh-delegate/bin/codex-dsh-delegate`. Read its README (`~/Documents/LLM/codex-dsh-delegate/README.md`) when you need the command contract or recovery details. It uses DSH ACP with `deepseek-flash` and `max`, keeps one session per task, and edits an isolated Git worktree. It does not add DSH to Codex's native subagent list.

## Delegate and review

1. Identify the Git repository root and read applicable project instructions. Define one bounded task with acceptance criteria. If the task depends on uncommitted files, select only the relevant relative file paths. For a non-Git project or an ambiguous project target, keep the work in Codex or clarify the target.
2. Write the task prompt to an absolute UTF-8 file in a private temporary location. Do not put credentials or unrelated sensitive content in it. Run `codex-dsh-delegate start --repo <absolute Git root> --task-file <absolute prompt file>` with `--include <relative file>` for each selected dirty input. The command may run for a while; retain its task ID and session ID.
3. After DSH settles, launch a separate **native Codex GPT subagent** to review the actual worktree diff and files. Give the reviewer the original task, acceptance criteria, repository and worktree paths, `diff_path`, and relevant test command. Ask it to inspect the complete diff, check correctness and scope, run or assess meaningful tests, and report actionable findings with file locations. Keep the reviewer read-only; it must not edit or integrate the change. Wait for its result. DSH's summary is not review evidence.
4. If the GPT reviewer finds problems, write its actionable feedback to an absolute UTF-8 file and run `codex-dsh-delegate continue --id <task ID> --feedback-file <absolute feedback file>`. This resumes the same DSH session. Every DSH revision invalidates the previous review: launch a native GPT review subagent again on the new complete diff and wait for its result.
5. The Codex main agent checks the reviewer's findings and relevant test evidence, then integrates only changes that pass the **final independent GPT subagent review**. Account for source drift and preserve unrelated dirty files. Report what changed and what was tested. Clean up the task worktree only after integration or an explicit discard decision.

If a native Codex GPT subagent cannot be launched or its review cannot complete, do not substitute DSH's self-assessment or claim the independent review passed; report the blocker and leave the change unintegrated. Use `status --id <task ID>` to inspect a task and `cancel --id <task ID>` for a running turn. If the invoking sandbox blocks DeepSeek API access, use the host's normal network permission flow for this authorized task. Do not change global Codex instructions, widen DSH file permissions, or retry with other credentials as a workaround. If DSH cannot complete the task, report the failure and continue with Codex when possible.
