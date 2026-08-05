# Custom Agent Suite

This folder contains structured agent definitions for the four-role agent army. Each role definition uses YAML frontmatter (name, role, tools, inputs, outputs, handoff, subagents) so the harness in `harness/` can load them programmatically.

## Roles

- `analysis/analysis.agent.md` — requirements, inventories, behavior extraction, traceability
- `design/design.agent.md` — architecture, data models, API contracts, migration plans
- `code/code.agent.md` — implements design as code changes on branches/PRs
- `test/test.agent.md` — generates and runs tests, verifies traceability, gates promotion

## Shared performance instructions

- `shared-performance.instructions.md`

All roles must apply the shared instructions to keep context windows focused and performant. The harness injects this file into every role's system prompt.

## Sub-agents

Sub-agents are intentionally narrow in scope. Role agents should delegate to these frequently to avoid oversized context and to parallelize work where possible.

- `analysis/subagents/legacy-inventory.subagent.md`
- `analysis/subagents/behavior-extraction.subagent.md`
- `design/subagents/api-contracts.subagent.md`
- `code/subagents/frontend-feature.subagent.md`
- `code/subagents/backend-service.subagent.md`
- `test/subagents/test-generation.subagent.md`
- `test/subagents/traceability-verification.subagent.md`

## Mission profiles

The legacy modernization workflow (requirements → architecture → build → verify) described in the top-level README is one mission profile the army can run; the roles are durable and reusable for other missions.
