# Agent: Requirements Discovery

## Purpose

Scan legacy source and produce traceable requirements documentation.

## Inputs

- COBOL programs and copybooks
- SQL scripts/procedures
- Java source/services

## Outputs

- `requirements/system-overview.md`
- `requirements/domain-requirements.md`
- `requirements/non-functional-requirements.md`
- `requirements/traceability-matrix.md`

## Operating instructions

- Follow `shared-performance.instructions.md`.
- Delegate source parsing and rule extraction to sub-agents instead of doing monolithic analysis.
- Merge only validated sub-agent summaries.
- Keep requirements atomic, testable, and source-traceable.

## Suggested sub-agent usage

- `subagents/legacy-inventory.subagent.md`
- `subagents/behavior-extraction.subagent.md`
