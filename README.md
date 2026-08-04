# agent-army

An **analysis / design / code / test agent army** that takes instructions as input from external sources (GitHub issues, webhooks, queues, or a local task folder) and drives them through a governed pipeline with human approval gates.

The repository contains:

- **`agents/`** — structured agent definitions (YAML frontmatter + role prompt) for the four roles, loadable by the harness or deployable directly as Copilot custom agents.
- **`harness/`** — a dependency-free Python harness: instruction intake, orchestrator with governance gates, role runners, pluggable sandbox layer, and a per-task ledger.
- **`tasks/`** — task envelopes (intake contract) and runtime state.
- **`docs/`** — harness design and the e2b.dev vs LangChain vs GitHub sandbox runtime evaluation.
- **`tests/`** — harness test suite.

## The four roles

1. **Analysis Agent** — ingests instructions + source material; produces requirements, inventories, behavior extraction, and traceability.
2. **Design Agent** — turns approved analysis into architecture, data models, API contracts, and migration/implementation plans.
3. **Code Agent** — implements design artifacts as code changes on a branch and opens PRs.
4. **Test Agent** — generates tests from requirements/contracts, runs suites, verifies traceability, and gates promotion between phases.

Each role delegates to narrow sub-agents (`agents/<role>/subagents/`) to keep context small, and applies `agents/shared-performance.instructions.md`.

## Instructions as input from another source

Tasks arrive as JSON envelopes conforming to `harness/schemas/task.schema.json` (task id, requesting system, target repo/refs, requested roles, goal, constraints, acceptance criteria, callback). Each role emits a result envelope (`harness/schemas/result.schema.json`) back to the source; the next role consumes it, enabling chained handoffs. Adapters in `harness/intake.py` support a local `tasks/inbox` folder (functional) plus GitHub-issue and webhook sources (stubs).

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
python3 -m unittest tests.test_harness
```

## Runtime: e2b.dev vs LangChain vs GitHub sandboxes

See [docs/runtime-evaluation.md](docs/runtime-evaluation.md). Summary: e2b and GitHub provide *sandboxes*, LangChain provides *orchestration*. The recommended hybrid is **LangGraph/deepagents as the harness**, **e2b as the execution sandbox** for the code/test roles, with a **GitHub-native mode** (Copilot coding agent) as a lightweight deployment target reusing the same role prompts. The sandbox layer (`harness/sandbox.py`) is pluggable: `LocalExecutor` works today; `E2BExecutor` and `GitHubRunnerExecutor` are integration stubs.

## Harness design

See [docs/harness.md](docs/harness.md) for the full design: intake layer, orchestrator with governance gates, role runners (pluggable `invoke` backend for LangChain deepagents, Copilot, or direct model APIs), sandbox layer, task ledger, and the traceability index.

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
