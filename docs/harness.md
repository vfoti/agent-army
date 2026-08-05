# Harness Design

The harness in `harness/` orchestrates the four agent roles (analysis, design, code, test) through governance gates. It is dependency-free Python (stdlib only) so it runs anywhere; its state machine maps 1:1 onto LangGraph (roles = nodes, gates = interrupts) if the LangChain deployment mode is adopted.

## Components

1. **Intake layer** (`harness/intake.py`) — adapters that normalize external instructions into Task envelopes and deliver Result envelopes back to the source:
   - `FolderIntake` — fully functional, watches `tasks/inbox/` and writes to `tasks/outbox/`. Use for local runs and testing.
   - `GitHubIssueIntake` — stub: labeled issues → tasks, results → issue comments.
   - `WebhookIntake` — stub: HTTP endpoint → tasks, results → callback URL.
2. **Contracts** (`harness/schemas/`, `harness/models.py`):
   - `task.schema.json` — task id, requesting system, target repo/refs, requested roles, goal, constraints, acceptance criteria, callback destination.
   - `result.schema.json` — role, status (`succeeded` / `failed` / `blocked` / `awaiting_approval`), artifacts (files, PRs, evidence), open questions, summary. Each role's result envelope is the next role's input, enabling chained handoffs.
3. **Orchestrator** (`harness/orchestrator.py`) — drives a task through its requested roles in order. After each gated role (analysis, design, test by default) it pauses with `awaiting_approval` until a human approval is recorded in the ledger; `advance()` is safe to call repeatedly to resume.
4. **Role runners** (`harness/runners.py`) — bind an `AgentDefinition` (loaded from `agents/<role>/<role>.agent.md` frontmatter) to an execution backend:
   - `PromptRoleRunner` — composes shared instructions + role prompt + task context and delegates to a pluggable `invoke` callable (LangChain/deepagents, Copilot coding agent, or direct model API).
   - `NullRoleRunner` — dry-run backend for testing the pipeline.
5. **Sandbox layer** (`harness/sandbox.py`) — pluggable `SandboxExecutor` for the code/test roles: `LocalExecutor` (functional), `E2BExecutor` and `GitHubRunnerExecutor` (integration stubs). See `docs/runtime-evaluation.md`.
6. **Task ledger** (`harness/ledger.py`) — per-task JSON state (results, approvals) under `tasks/ledger/`, enabling pause/resume across processes. The recorded artifacts form the traceability index: task → requirement → design → PR → test evidence.

## CLI

```bash
python3 -m harness agents                       # list loaded role definitions
python3 -m harness run <task.json>              # run/resume a task (dry-run runners)
python3 -m harness approve <task_id> <role> <approver>
python3 -m harness status <task_id>
```

## Tests

```bash
python3 -m unittest tests.test_harness
```

## LangChain / deepagents integration

To run roles with LangChain's deepagents harness, implement the `invoke` callable of `PromptRoleRunner` with a deep agent configured from the `AgentDefinition`: its composed system prompt (`build_system_prompt`), its `subagents` list as deepagents subagents, and role-scoped tools (analysis/design: read-only repo + doc writing; code: sandboxed write + git; test: sandboxed execution). Replace the orchestrator with a LangGraph graph using interrupts at the gates when durable, multi-process execution is needed, keeping the same Task/Result contracts.
