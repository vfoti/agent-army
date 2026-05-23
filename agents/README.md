# Custom Agent Suite

This folder contains custom agent definitions and instruction files for legacy modernization.

## Core agents

- `requirements-discovery.agent.md`
- `architecture-design.agent.md`
- `build-delivery.agent.md`

## Shared performance instructions

- `shared-performance.instructions.md`

All core agents must apply the shared instructions to keep context windows focused and performant.

## Sub-agents

Sub-agents are intentionally narrow in scope. Core agents should delegate to these frequently to avoid oversized context and to parallelize work where possible.

- `subagents/legacy-inventory.subagent.md`
- `subagents/behavior-extraction.subagent.md`
- `subagents/api-contracts.subagent.md`
- `subagents/frontend-feature.subagent.md`
- `subagents/backend-service.subagent.md`
