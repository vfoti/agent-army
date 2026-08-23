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
   - `PromptRoleRunner` — composes shared instructions + role prompt + task context and delegates to a pluggable `invoke` callable (Copilot coding agent or a direct model API).
   - `AnthropicRoleRunner` (`harness/anthropic_runner.py`) — default backend; hand-rolled tool loop over the Anthropic Messages API.
   - `DeepAgentsRoleRunner` (`harness/deepagents_runner.py`) — optional backend running the role inside LangChain's Python `deepagents` harness. Selected with `AGENT_ARMY_ROLE_RUNNER=deepagents`.
   - `NullRoleRunner` — dry-run backend for testing the pipeline.
5. **Sandbox layer** (`harness/sandbox.py`) — pluggable `SandboxExecutor` for the code/test roles: `LocalExecutor`, ephemeral-container `DockerExecutor`, and persistent task-scoped `DockerSandboxExecutor` are functional; `E2BExecutor` and `GitHubRunnerExecutor` are integration stubs. Docker Sandboxes are reused by code and test, recovered by deterministic task name after service restarts, and removed at task completion unless retention is enabled.
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
python3 -m unittest tests.test_harness tests.test_phase2 tests.test_deepagents_runner
```

## Security guards

Both role runners execute tools through `RoleToolbox` (`harness/tools.py`), which owns every guard that must hold regardless of which agent framework drives the model:

- only the tools named in the role's agent-definition frontmatter are exposed;
- repository paths are confined to the task workspace;
- sandbox commands use argv rather than a host shell;
- per-run tool-call counts, command timeouts, and output size are capped;
- configured API tokens are redacted from tool results;
- `database_query` accepts a single read-only statement, and database credentials come from CLI-native profiles rather than model tool arguments.

## LangChain / deepagents integration

Roles can optionally run inside LangChain's **Python** [`deepagents`](https://github.com/langchain-ai/deepagents) harness via `DeepAgentsRoleRunner` (`harness/deepagents_runner.py`). The JavaScript sibling `deepagentsjs` was evaluated and rejected — see [runtime-evaluation.md](runtime-evaluation.md#rejected-deepagentsjs).

```bash
pip install deepagents langchain-anthropic
export AGENT_ARMY_ROLE_RUNNER=deepagents      # default: anthropic
python3 -m harness.service --folder --once
```

**What deepagents owns:** the inner agent loop only — todo-based planning, context management, and executable delegation to the narrow sub-agents that `agents/<role>/subagents/*.subagent.md` otherwise only describe in prose. `load_subagents()` (`harness/agent_loader.py`) maps each `*.subagent.md` file onto a deepagents sub-agent, named by its file stem (for example `legacy-inventory`) and confined to the role's own directory.

**What deepagents does not own:** intake, governance gates, the ledger, the Task/Result envelope contract, and the security guards. `Orchestrator` is unchanged; it is deliberately not replaced by a LangGraph graph, because the gates plus the ledger already provide resumption at lower cost.

Because deepagents follows a "trust the LLM" model, the runner does not inherit its defaults:

- Its built-in filesystem tools are bound to the in-memory `StateBackend`, which never touches the host. That is scratch space for the agent's own context management; everything durable is written through the guarded harness tools and referenced by workspace-relative path in the result envelope.
- The only tools that reach the workspace, a shell, or a database are the allow-listed `RoleToolbox` tools, wrapped as LangChain structured tools. Sub-agents inherit exactly the role's tools, so delegation never widens what a role may do.
- `BudgetGuard` is enforced *per model call* through a LangChain callback handler, not once per role: a deep agent makes many calls per role, so a per-role check would let ceilings be overrun. `AGENT_ARMY_DEEPAGENTS_RECURSION_LIMIT` (default 50) additionally bounds the graph steps in a single role.

`deepagents` and its LangChain dependencies are imported lazily, so the harness remains stdlib-only for anyone who does not opt in.

### Comparing the two runners on the pilot

`tests/test_deepagents_runner.py` runs `tasks/examples/hello-world-001.json` through both runners with a stubbed chat model and asserts that they produce an identical result envelope and identical budget accounting, that gate behaviour is unchanged, and that the ceilings and caps block as expected. The deepagents cases are skipped unless the package is installed:

```bash
python3 -m unittest tests.test_deepagents_runner
```

Before promoting `deepagents` to the default, run the pilot live under both runners and compare artifacts, recorded cost in `tasks/ledger/`, and gate behaviour. The Anthropic runner remains the default until that comparison justifies switching.
