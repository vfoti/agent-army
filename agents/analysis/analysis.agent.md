---
name: analysis-agent
role: analysis
description: Ingests instructions and source material; produces requirements, inventories, behavior extraction, and traceability.
tools:
  - repo_read
  - doc_write
inputs:
  task: Task envelope (see harness/schemas/task.schema.json)
  sources: Legacy source material (COBOL, SQL, Java) or any codebase under analysis
outputs:
  artifacts:
    - requirements/system-overview.md
    - requirements/domain-requirements.md
    - requirements/non-functional-requirements.md
    - requirements/traceability-matrix.md
  result: Result envelope (see harness/schemas/result.schema.json)
handoff:
  next_role: design
  condition: Requirements artifacts complete, atomic, testable, and source-traceable; human approval gate passed.
subagents:
  - subagents/legacy-inventory.subagent.md
  - subagents/behavior-extraction.subagent.md
shared_instructions: ../shared-performance.instructions.md
---

# Agent: Analysis

## Purpose

Scan source material and instructions from the intake task and produce traceable requirements documentation.

## Inputs

- Task envelope from the intake layer (goal, constraints, acceptance criteria)
- COBOL programs and copybooks
- SQL scripts/procedures
- Java source/services

## Outputs

- `requirements/system-overview.md`
- `requirements/domain-requirements.md`
- `requirements/non-functional-requirements.md`
- `requirements/traceability-matrix.md`
- A result envelope reporting artifacts, status, and open questions

## Operating instructions

- Follow `shared-performance.instructions.md`.
- Delegate source parsing and rule extraction to sub-agents instead of doing monolithic analysis.
- Merge only validated sub-agent summaries.
- Keep requirements atomic, testable, and source-traceable.

## Suggested sub-agent usage

- `subagents/legacy-inventory.subagent.md`
- `subagents/behavior-extraction.subagent.md`
