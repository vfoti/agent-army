# Runtime Evaluation: e2b.dev vs LangChain vs GitHub Sandboxes

These options are not strictly interchangeable — e2b and GitHub provide *sandboxes*, LangChain provides *orchestration* — so they are evaluated along those axes.

## e2b.dev (Firecracker microVM sandboxes)

**Pros**
- Purpose-built for agent code execution; fast VM spin-up; Python/JS SDKs.
- Per-agent isolated filesystems; long-running sessions.
- Custom templates: prebuild an image with JDK, Node/Angular, and COBOL tooling for the code/test roles.

**Cons**
- Paid service; orchestration and GitHub integration are yours to build.
- Artifacts must be shuttled in/out of the sandbox.

**Harness fit:** implement `E2BExecutor` (`harness/sandbox.py`) with the `e2b` SDK, creating a sandbox from the toolchain template per code/test run.

## LangChain (LangGraph + deepagents)

Not a sandbox — the orchestration/harness layer.

**Pros**
- LangGraph gives durable, stateful multi-agent graphs matching the analysis → design → code ⇄ test pipeline, with human-in-the-loop interrupts at the governance gates.
- deepagents provides planning, subagent spawning, and a filesystem abstraction matching this repo's prompt structure.
- LangSmith for tracing and eval.

**Cons**
- Code execution still needs a backend — pair with e2b or Docker.
- Adds a framework dependency and a Python service to maintain.

**Harness fit:** plug a deep agent into `PromptRoleRunner.invoke`; optionally replace `Orchestrator` with a LangGraph graph, keeping the Task/Result contracts.

## GitHub sandboxes (Copilot coding agent / Actions runners)

**Pros**
- Zero infrastructure; native repo/PR/review integration — governance gates map directly to PR approvals.
- The `agents/*.agent.md` definitions are close to Copilot custom-agent format; instructions arrive by assigning issues to the agent, matching the "input from another source" requirement with no adapter code.

**Cons**
- Less control over inter-agent orchestration (chaining via issues/PRs rather than an in-process graph).
- Execution environment constraints; harder to run tight multi-agent loops or custom tool servers.

**Harness fit:** implement `GitHubRunnerExecutor` via `workflow_dispatch`, and `GitHubIssueIntake` via webhooks; or bypass the harness entirely and deploy the role prompts as Copilot custom agents.

## Recommendation: hybrid

- **LangGraph/deepagents as the harness** for orchestration and state.
- **e2b as the execution sandbox** for the code and test roles.
- **GitHub-native mode** (Copilot coding agent) as a lightweight deployment target reusing the same role prompts.

The structured frontmatter agent definitions in `agents/` keep the prompts portable across all three.
