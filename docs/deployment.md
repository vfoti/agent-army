# Deployment (Phase 2 decisions)

Locked decisions:

| # | Decision | Choice |
| --- | --- | --- |
| D1 | Model backend | Anthropic Messages API (`harness/anthropic_runner.py`) |
| D2 | Sandbox | Local Docker, **ephemeral container per command** (`DockerExecutor`) |
| D3 | Intake | GitHub issues labeled `agent-task`; approvals via `/approve <role>` comments; results as issue comments |
| D4 | Harness host | Always-on local machine (Docker Compose service) |
| D5 | Secrets & budgets | Env vars; hard token/cost ceilings per role and per task (`BudgetGuard`) |
| D6 | State | File-based ledger in a container volume (`tasks/ledger/`) |
| D7 | Pilot | `tasks/examples/hello-world-001.json` |

## D2 sub-decision: why ephemeral-per-command (real-world scenario)

The Test agent runs `mvn test` for a Spring slice. That command downloads
dependencies into `/workspace/.m2` (kept — it's on the bind mount), but may
also leave a stray background process and polluted env vars. With one
long-lived container per task, the next command inherits that dirty state,
and a hung test process can wedge the entire task. With ephemeral
containers, the next command (`ng test` for the Angular slice) starts from a
clean process environment; only workspace files carry over, and a runaway
test dies with its container at the timeout. Cost: ~1s startup per command —
negligible next to multi-minute build/test runs. If a mission later needs an
interactive stateful session (e.g. a running dev server), add a
session-scoped executor variant then.

## Setup

```bash
# 1. Secrets (D5) — put these in a .env file next to docker-compose.yml
ANTHROPIC_API_KEY=sk-ant-...
GITHUB_TOKEN=ghp-...            # repo scope on the intake repo
AGENT_ARMY_REPO=vfoti/agent-army

# Optional tuning
AGENT_ARMY_MODEL=claude-sonnet-4-5
AGENT_ARMY_BUDGET_USD_PER_TASK=5.0
AGENT_ARMY_BUDGET_USD_PER_ROLE=2.0
AGENT_ARMY_BUDGET_TOKENS_PER_TASK=500000
AGENT_ARMY_POLL_INTERVAL=60

# 2. Create the intake label on the repo (once)
gh label create agent-task --repo "$AGENT_ARMY_REPO"

# 3. Start the always-on service (D4)
docker compose up -d --build
docker compose logs -f
```

Without Docker (bare local run): `python3 -m harness.service`
(needs `pip install anthropic`). For offline testing:
`python3 -m harness.service --folder --dry-run --once`.

## Docker Sandboxes microVM backend

Run the harness directly on an Ubuntu 24.04 or newer host with KVM enabled.
If the host is a VM, enable nested virtualization. Install `docker-sbx`, add
the service user to the `kvm` group, run `sbx login`, and verify `sbx ls`
before starting the service. Docker Desktop is not required.

```bash
AGENT_ARMY_SANDBOX_BACKEND=docker-sandbox
AGENT_ARMY_SANDBOX_TEMPLATE=shell
AGENT_ARMY_SANDBOX_CLONE=false
AGENT_ARMY_SANDBOX_RETAIN=false
AGENT_ARMY_SANDBOX_TIMEOUT=600
AGENT_ARMY_SANDBOX_MAX_TOOL_CALLS=20
AGENT_ARMY_SANDBOX_MAX_OUTPUT_CHARS=50000
python3 -m harness.service --folder
```

Direct mode shares the host workspace read-write. Set
`AGENT_ARMY_SANDBOX_CLONE=true` for a private in-VM clone when parallel tasks
must be isolated. A deterministic sandbox name allows recovery after a service
restart. On successful task completion the VM is removed; with retention
enabled it is stopped and kept for debugging.

Configure network allowlists with `sbx policy` and credentials with
`sbx secret`; credentials are not forwarded by the harness. The service checks
the CLI, workspace, KVM, daemon, and authentication at startup. Authentication
errors appear in the `sbx ls` diagnostic.

The supplied Compose deployment intentionally remains on the legacy
ephemeral-container backend and mounts the host Docker socket. Do not select
`docker-sandbox` there unless the container has been explicitly provisioned
with the `sbx` daemon access and `/dev/kvm`; host deployment is preferred.

For a pilot, use folder intake and a disposable Git checkout first. Confirm
workspace changes, network policy, cleanup, and ledger state before enabling
GitHub issue intake.

Troubleshooting:

- `sbx is not installed`: install the Docker Sandboxes CLI on the harness host.
- `KVM ... unavailable`: enable virtualization and grant the user `/dev/kvm`.
- `unavailable or unauthenticated`: run `sbx login`, then verify `sbx ls`.
- Retained or failed sandboxes: inspect with `sbx ls` and remove with
  `sbx rm --force <name>`.

## Workflow

1. Open a GitHub issue, label it `agent-task`. Optionally include a
   ```json fenced task envelope in the body (see
   `harness/schemas/task.schema.json`); otherwise the title becomes the goal
   and all four roles run.
2. The service ingests it (relabels to `agent-task:accepted`), runs the
   analysis role, and comments the result envelope on the issue.
3. At each governance gate, comment `/approve analysis` (then `design`,
   `test`) to resume the pipeline.
4. Budget guards stop any role that would exceed its cost/token ceiling and
   report `blocked` on the issue; raise the env-var ceilings and re-run to
   continue.

## Hello-world pilot (D7)

```bash
mkdir -p tasks/inbox && cp tasks/examples/hello-world-001.json tasks/inbox/
python3 -m harness.service --folder --dry-run --once   # or drop --dry-run with an API key
python3 -m harness approve hello-world-001 analysis you
python3 -m harness.service --folder --dry-run --once   # resumes at design
```

Or via GitHub: open an issue titled "Hello world pilot", paste the JSON from
`tasks/examples/hello-world-001.json` in a ```json block, and label it
`agent-task`.
