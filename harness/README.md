# Coding-agent harness

This package is a small, dependency-free reference runtime for the agent suite.
It keeps the model provider behind `ModelAdapter`, while every repository side
effect goes through `ToolPolicy` and `WorkspaceTools`.

## Run a pilot

```python
from harness import AgentHarness, RunRequest

class Model:
    def complete(self, messages, tools):
        return {"content": "inspect the repository", "tool_calls": []}

result = AgentHarness(".", Model()).run(RunRequest("inventory one pilot domain"))
```

The runtime discovers `agents/*.agent.md`, applies the shared instruction file,
records JSONL audit events under `.harness/events.jsonl`, and enforces the
Session 1 → 2 → 3 approval gates. Skills are optional and loaded only when
requested by name.

## Contract and controls

- Supported adapters return `content` and optional `tool_calls`; provider SDKs
  are not allowed to execute tools directly.
- Reads, searches, and new-file writes are available in the workspace.
  Overwrites and shell commands require an approval token.
- Shell commands are allowlisted and run with a timeout from the repository
  root. Paths are resolved and rejected if they escape the workspace.
- Cancellation is checked between model turns and tool calls.
- Runs, model responses, tool requests, phase approvals, and failures are
  append-only audit events, allowing inspection and replay of the context.
- The harness intentionally does not claim that model-generated text is safe:
  authorization belongs to the policy/tool boundary.

The repository's existing README remains the source of truth for artifact
names, governance, and traceability requirements.
