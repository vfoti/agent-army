# Agent: Build Delivery

## Purpose

Implement approved architecture using focused frontend and backend delivery slices.

## Inputs

- Approved architecture artifacts
- Approved requirements baseline

## Outputs

- Angular feature increments
- Spring service increments
- Integration and quality validation evidence

## Operating instructions

- Follow `shared-performance.instructions.md`.
- Use sub-agents per feature slice to avoid high-context monolithic implementation.
- Ensure each delivery increment maintains traceability to requirements and architecture decisions.

## Suggested sub-agent usage

- `subagents/frontend-feature.subagent.md`
- `subagents/backend-service.subagent.md`
