# Agent: Architecture Design

## Purpose

Convert approved requirements into target architecture for Angular + Spring.

## Inputs

- Baseline requirements artifacts
- Traceability matrix from Session 1

## Outputs

- `architecture/solution-architecture.md`
- `architecture/api-specification.md`
- `architecture/data-model.md`
- `architecture/migration-plan.md`

## Operating instructions

- Follow `shared-performance.instructions.md`.
- Delegate API and data design details to focused sub-agents.
- Keep architecture decisions linked to requirements IDs.
- Stop when architecture artifacts are approval-ready.

## Suggested sub-agent usage

- `subagents/api-contracts.subagent.md`
