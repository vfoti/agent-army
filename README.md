# agent-army

An **analysis / design / code / test agent army** that takes instructions as input from external sources (GitHub issues, webhooks, queues, or a local task folder) and drives them through a governed pipeline with human approval gates.

The repository contains:

- **`agents/`** — structured agent definitions (YAML frontmatter + role prompt) for the four roles, loadable by the harness or deployable directly as Copilot custom agents.
- **`harness/`** — a stdlib-only Python harness: instruction intake, orchestrator with governance gates, role runners, guarded role-scoped tools (`tools.py`), pluggable sandbox layer, env-var configuration (`config.py`), token/cost ceilings (`budget.py`), an always-on service loop (`service.py`), and a per-task ledger. Third-party dependencies are optional and lazily imported: the `anthropic` SDK for `harness/anthropic_runner.py`, and `deepagents` + `langchain-anthropic` for the opt-in `harness/deepagents_runner.py`.
- **`tasks/`** — task envelopes (intake contract) and runtime state.
- **`docs/`** — harness design, deployment, and the e2b.dev vs LangChain vs GitHub sandbox runtime evaluation.
- **`tests/`** — harness test suite (`tests/test_harness.py`, `tests/test_phase2.py`, `tests/test_deepagents_runner.py`).
- **`Dockerfile` / `docker-compose.yml`** — the always-on containerized deployment of the harness service.

## The four roles

1. **Analysis Agent** — ingests instructions + source material; produces requirements, inventories, behavior extraction, and traceability.
2. **Design Agent** — turns approved analysis into architecture, data models, API contracts, and migration/implementation plans.
3. **Code Agent** — implements design artifacts as code changes on a branch and opens PRs.
4. **Test Agent** — generates tests from requirements/contracts, runs suites, verifies traceability, and gates promotion between phases.

Each role delegates to narrow sub-agents (`agents/<role>/subagents/`) to keep context small, and applies `agents/shared-performance.instructions.md`.

For DB2 on z/OS to PostgreSQL work, role-scoped tools support read-only
queries, schema extraction, and transactional PostgreSQL SQL migrations.
Connection setup and client requirements are documented in
[docs/deployment.md](docs/deployment.md#db2-to-postgresql-database-tools).

## Instructions as input from another source

Tasks arrive as JSON envelopes conforming to `harness/schemas/task.schema.json` (task id, requesting system, target repo/refs, requested roles, goal, constraints, acceptance criteria, callback). Each role emits a result envelope (`harness/schemas/result.schema.json`) back to the source; the next role consumes it, enabling chained handoffs. Adapters in `harness/intake.py` support a local `tasks/inbox` folder (`FolderIntake`) and GitHub issues (`GitHubIssueIntake` — polls the `agent-task` label, reads an optional fenced JSON envelope from the issue body, parses `/approve <role>` comments, and posts results back as comments). `WebhookIntake` remains an integration stub.

## Quick start

```bash
# list loaded role definitions
python3 -m harness agents

# run a task (dry-run role runners); it pauses at the analysis approval gate
python3 -m harness run tasks/examples/modernize-billing-001.json

# approve the gate and resume
python3 -m harness approve modernize-billing-001 analysis <your-name>
python3 -m harness run tasks/examples/modernize-billing-001.json

# run the tests
python3 -m unittest tests.test_harness tests.test_phase2 tests.test_deepagents_runner

# run the always-on service loop (folder intake, offline, single cycle)
python3 -m harness.service --folder --dry-run --once
```

The Phase 2 pilot envelope is `tasks/examples/hello-world-001.json`.

## Runtime: e2b.dev vs LangChain vs GitHub sandboxes

See [docs/runtime-evaluation.md](docs/runtime-evaluation.md). The sandbox layer is pluggable: local execution, ephemeral Docker containers, and persistent task-scoped Docker Sandbox microVMs work today; e2b and GitHub runners remain integration stubs. Docker Sandboxes are the recommended VM-isolated local backend.

## Deployment

See [docs/deployment.md](docs/deployment.md) for the always-on Docker Compose deployment, GitHub-issue intake workflow (`agent-task` label, `/approve <role>` comments), budget guards, and the hello-world pilot.

## Harness design

See [docs/harness.md](docs/harness.md) for the full design: intake layer, orchestrator with governance gates, role runners, guarded role-scoped tools, sandbox layer, task ledger, and the traceability index.

Roles run through the Anthropic runner by default. Setting `AGENT_ARMY_ROLE_RUNNER=deepagents` (with `pip install deepagents langchain-anthropic`) runs each role inside LangChain's **Python** [`deepagents`](https://github.com/langchain-ai/deepagents) harness instead, which adds planning and turns the `agents/<role>/subagents/*.subagent.md` definitions into executable delegation. deepagents owns only the inner agent loop — intake, gates, the ledger, the Task/Result contracts, and the security guards stay in the harness. The JavaScript sibling `deepagentsjs` was evaluated and rejected on language-mismatch grounds; see [docs/runtime-evaluation.md](docs/runtime-evaluation.md#rejected-deepagentsjs).

## Governance rules

- The pipeline pauses after **analysis**, **design**, and **test** for human approval (`python3 -m harness approve ...`) before the next role starts.
- Preserve full traceability from delivered features back to source evidence: the ledger records task → requirement → design → PR → test evidence.
- Prefer incremental delivery slices by business capability.

## Mission profile: legacy modernization

The original mission for this army is modernizing legacy COBOL, SQL, and Java systems into a consolidated **Angular** frontend and **Spring** middleware. Mapped to the roles:

| Phase | Role | Primary outputs |
| --- | --- | --- |
| Source scanning & requirements | analysis | `requirements/system-overview.md`, `requirements/domain-requirements.md`, `requirements/non-functional-requirements.md`, `requirements/traceability-matrix.md` |
| Architecture & solution design | design | `architecture/solution-architecture.md`, `architecture/api-specification.md`, `architecture/data-model.md`, `architecture/migration-plan.md` |
| Build & delivery | code | Angular feature increments, Spring service increments, PRs per slice |
| Verification & promotion | test | test suites, execution evidence, traceability verification report |

An example task envelope for this mission is provided at `tasks/examples/modernize-billing-001.json`.
