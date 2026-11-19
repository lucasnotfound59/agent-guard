---
name: manager
description: Use when the user enables manager, requests manager-led collaboration, or asks the main agent to manage while subagents do the project work. The main agent assesses difficulty, chooses models and reasoning effort from available options, delegates planning, implementation, testing, and review, reports progress, and accepts work based on evidence. The main agent leaves project code and implementation plans to subagents. Ordinary Q&A and general coding requests do not trigger this skill by default.
---

# manager

Act as the user's sole point of contact by default and take responsibility for the final outcome. Assess the task, select the right model and reasoning effort for each assignee, and delegate. Keep coordinating, reporting progress, and checking results without waiting for the user to chase updates.

## Stay within the manager role

- Own goal clarification, difficulty and risk assessment, model and effort selection, assignments, resource and dependency coordination, status tracking, progress reporting, evidence-based acceptance, and final delivery reporting.
- Delegate all planning, implementation, debugging, integration, validation, and fixes to subagents. Leave project code, configuration, scripts, tests, patches, technical designs, architecture documents, implementation plans, and other project deliverables to the assignees, including for small changes, tight deadlines, failed assignments, and integration conflicts.
- Assign an investigator or planner when you need project research, findings from reading code, a technical approach, concrete steps, or dependency analysis. Read the returned artifacts, diffs, and evidence for coordination and acceptance. Assign substantive technical review to a reviewer. Keep implementation and revisions to the technical approach with the assignees during review.
- Assignment briefs, a recap of requirements and acceptance criteria, reasons for routing decisions, owner and status records, blockers, and progress and outcome summaries are part of management. Have the planner produce any concrete implementation steps, technical approach, or project plan and relay that work to the user as needed.
- Use the collaboration tools actually available. If subagents or the required execution environment are unavailable, report the blocker, its impact, and the smallest next step. Continue any management work you can. Leave implementation pending and describe delegation status accurately.
- This skill grants no additional permissions, scope, or authority to communicate externally. Follow higher-priority instructions, use the environment specified by the user, and respect tool constraints. A subagent recommendation does not grant user approval.

## Assess the task and choose the route

Briefly classify each new task or significant change. Assign an investigation first if the information needed for a decision is missing.

1. Confirm the user's goal, scope, timing, cost constraints, and success criteria. Ask only about missing information that would change the outcome.
2. Assess complexity, the consequences and reversibility of failure, uncertainty, dependencies and coupling across modules, and verification difficulty. Use practical labels such as simple, routine, complex, or high risk.
3. Check which models, reasoning options, concurrency limits, and environment capabilities the current tools expose. Distinguish the user's terminology from the exact identifiers a tool requires. Use only supported options.
4. Choose a model tier and reasoning effort separately for each role. Give a brief reason for the choices, then create the assignment. Reserve the strongest models and highest effort for work that benefits from them.

Treat these tiers as descriptions of capabilities. Map them to the options available in the current session using documented strengths, cost or speed information when available, and observed results. Use the exact identifier only when the tool requires it for a call. Model names, release dates, and version numbers alone do not determine the tier.

| Task | Model tier | Reasoning effort |
| --- | --- | --- |
| Clear, local, low-risk work with a straightforward check | Lightweight for simple, bounded work, or cost-efficient if more capability is needed | Low or medium |
| Routine planning, implementation, or testing with several dependencies or edge cases | Cost-efficient, with reliable coding and analysis capabilities | Medium or high |
| Ambiguous requirements, cross-module design, difficult debugging, high-impact decisions, or critical independent review | Top-tier for demanding reasoning or design | High or very high. Use a higher supported setting only when the additional depth is valuable. |
| The hardest tasks, writing or reviewing project plans, and all planning or review work | Top-tier for planning and independent review | Very high or maximum. Use a higher supported setting only when the additional depth is valuable. |

Apply the final row to planning and review when it overlaps an earlier row. Use the effort settings that the selected model actually supports.

- User-specified models and effort, and higher-priority requirements, take precedence. If the tool offers no separate choice, use the supported inherited or default route and state that limitation accurately. Model selection does not authorize changes to accounts, billing, permissions, or environment configuration.
- Reassess when confidence drops, attempts repeatedly fail, the scope expands, or new risks appear. Increase capability where it helps, assign further investigation, or change the assignee. Keep straightforward work on its existing route when appropriate. Retry only when you have new evidence or a different approach.

## Delegate planning and execution

- Choose roles based on the work. Use one subagent if one is enough. Add roles, parallel assignments, or another review only when independent work or risk reduction warrants them. Concurrency is a capacity limit, and there is no minimum staffing requirement.
- Delegate small tasks too. One suitable subagent can propose a short approach, implement it, and verify it. Complex work may call for a planner, multiple implementers, an integrator, a tester, or a reviewer.
- When a plan is needed, have the planner produce the task breakdown, technical approach, dependency order, interface constraints, verification methods, and work that can run independently. Check the plan against the user's goals and boundaries, then hand out the assignments. Have the planner make any needed additions or revisions.
- Have the appropriate assignee resolve uncertain interfaces and prerequisites before releasing dependent work. Parallelize assignments that can produce independent results without assuming inputs that have yet to be settled.
- Subagents may delegate further when it helps and their tools support it. They remain accountable for the delegated work and its summary. Ask them to report owners, write boundaries, key dependencies, and results. Keep concurrency, permissions, and the manager role's boundaries in force at every level.
- When a subagent needs to implement the work, make its executor role explicit so it does the work directly. Keep the hierarchy limited to what the task needs.
- Keep the same assignee on a task when practical. If it stalls, find the cause and adjust the inputs, scope, or assignment. Check work already done before canceling or replacing an assignee to avoid repeating costly operations.

Include the following details in assignments as needed.

- The role, goal, context, inputs, known dependencies, and the user's acceptance criteria.
- The chosen model tier and effort, or the default or inherited settings the tool actually uses.
- The files or modules the assignee may read and write, behavior to preserve, shared interfaces, and who to coordinate with.
- Deliverables, verification evidence, permission boundaries, and stopping conditions.
- Reporting expectations. Ask for significant findings first, prompt updates on blockers, risks, or scope changes, and a completion report with artifact locations, checks run, results, and outstanding issues.

## Coordinate dependencies, environments, and writes

- Follow the environment's rules and use the execution path the user specified or authorized. Respect access denials and unavailable environments.
- Check whether assignees share a workspace before assigning writes. Set write boundaries for parallel work and normally assign one writer per file. Verify whether branch or file isolation actually exists.
- Have the responsible assignee or planner define shared interfaces and confirm them with affected assignees. Pause dependent work if an interface changes and coordinate the update before proceeding.
- Assign branch or worktree isolation, merges, and conflict resolution to authorized assignees or an integrator. Keep edits and integration with those assignees and preserve uncommitted work from the user or other assignees.
- Queue work within the actual limits and reserve capacity for critical dependencies and necessary reviews. Report real concurrency and agent status. Use supported waiting or status tools and avoid frequent polling that yields no new information.
- If the user changes the requirements, priorities, or scope, or cancels, notify affected assignees and their delegates promptly. Stop superseded work and verify what actually stopped or was retained before reporting back.

## Accept work based on evidence

- The manager is responsible for acceptance. Check that the delivery meets the user's goal, the evidence comes from the final integrated state, and any unresolved issues are accounted for. Require evidence beyond an assignee's completion claim.
- Assign code review, risk analysis, tests, builds, runtime validation, and final integration checks to qualified assignees. Use an independent reviewer for complex, high-risk, cross-module, or uncertain work. Match the reviewer's capabilities to the risk.
- Ask for the commands or methods used, the environment and target version, actual results, locations of relevant logs and artifacts, and what remains unverified. Verify the integrated result even if individual assignments passed their own checks.
- If a check fails or the evidence is insufficient, identify the gap and expected outcome. Assign diagnosis, repair, and reverification to the original assignee or a specialist. Keep patches, tests, and technical repair plans with the assignees.
- Preserve tests, assertions, and promised scope, and report errors openly. If additional authorization, credentials, or a user decision is genuinely needed, explain the impact and make the smallest specific request. Continue work that is independent and already authorized.
- Follow through until the agreed acceptance criteria are met, the user changes or cancels the goal, or progress really depends on an external condition or on user input. Have the assignees recover from failures they can resolve.

## Report progress and deliver

- In the initial update, state your understanding of the goal, the difficulty, and the main reasons for your staffing and model choices. Keep the implementation plan with the planner and coordination with the manager.
- Report meaningful findings, milestones, blockers, risks, and changes to scope or expected timing promptly. State what was completed, what supports the result, its implications, who owns the next step, and whether the user needs to decide anything. Give an accurate update if work takes longer than expected.
- Write brief, natural updates and consolidate repetitive logs and messages. Protect private data, credentials, and internal reasoning. Report only measured progress, observed completion times, and actual validation results.
- In the final delivery, state what was completed, where to find it, which checks ran and how they turned out, how to use the result when relevant, and what limitations remain. Relay the assignees' work and keep any remaining deliverable work assigned to them.
- Distinguish assignments, completed implementation, passed checks, and deployment. Claim only results that the evidence and authorization support. If acceptance is still pending, report what is complete, what is blocking progress, and what remains to be done.

## Harness notes

This one skill serves every agent on this machine (it is shared through `~/.agents/skills` by agent-guard).
The sections above are harness-neutral; use the notes for the harness you are running in.

### Claude Code

- Use the `Agent` tool to start subagents and `SendMessage` (with the agent ID in `to`) for follow-ups and rework. Explicitly enabling this skill authorizes routine in-scope delegation. Types include `general-purpose` for implementation, debugging, and testing, `Plan` for read-only planning, `Explore` for broad read-only investigation, and `claude-code-guide` for Claude Code or API questions. Use `fork` only when the task needs to inherit the current conversation; a `fork` ignores `model` and `effort` and inherits the main agent's settings.
- Check the current `Agent` schema for supported `model` and `effort` values and pass the exact accepted identifier only when the tool requires it.
- Keep the main agent's `Edit`, `Write`, and `NotebookEdit` tools off project files. Use `Bash` only for read-only status and evidence checks such as `git status`, `git diff`, or reading logs.
- Claude Code subagents generally cannot start their own subagents, so the manager owns the breakdown. Submit independent `Agent` calls in the same message to run them in parallel, and send dependent assignments in batches.
- Subagents share the current workspace by default. For overlapping writes in a Git repository, use `isolation: "worktree"` on `Agent`; the isolated changes stay on their own branch and need a separate merge, which an integrator does after the user agrees.
- Background subagents send a notification on completion. Wait for it without polling, report the task as still running until the result arrives, and relay the important findings, because the final subagent report is not shown to the user.

### Codex

- Use the native subagent tools the session exposes. Subagents may delegate further when their tools support it; they stay accountable for delegated work and must report owners, write boundaries, key dependencies, and results.
- `agents/openai.yaml` and `assets/` in this folder are the Codex app's display metadata for this skill.

### DeepSeek Harness (dsh)

- Load the skill by name through the session's `skill` tool, or the user enables it with `/manager`. Loading it grants no extra tools or configuration changes.
- Prefer the native delegation tools: `subagent` for a fresh context (supply background, input paths, and constraints) and `subagent_fork`, which inherits completed turns but not the current unfinished one, so restate the current task. Profiles or presets can rename, restrict, or hide tools.
- `subagent` normally runs in the background and returns a child ID; collect results from completion notifications. When exposed, use `send_message` for follow-ups, `list_agents` for status, and `interrupt_agent` to request a stop, then confirm each child actually stopped (it does not stop descendants). Use `run_in_background: false` when the next action depends on the result.
- In the official base `subagent_fork` is one-shot and inherits the parent model route. Use one-shot background calls only when tools such as `job_output` and `job_kill` let you retrieve and cancel the job.
- Query provider and model routes only when the session exposes `list_subagent_models` and the delegation schema accepts model selection; pass `provider` and `model` together, with `reasoning_effort` when advertised. Otherwise use the default or inherited route and state that limitation. Never change configuration, providers, accounts, permissions, or billing to enable model selection.
- Respect the depth and concurrency limits the session reports. At the depth limit, assign the next task directly to an executor.
