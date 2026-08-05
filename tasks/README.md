# Tasks

Instruction intake for the agent army.

- `examples/` — example task envelopes (see `harness/schemas/task.schema.json`)
- `inbox/` — drop task JSON files here for the folder-based intake adapter (gitignored)
- `outbox/` — result envelopes delivered by roles (gitignored)
- `ledger/` — per-task pipeline state, including governance-gate approvals (gitignored)

## Running a task locally

```bash
python3 -m harness run tasks/examples/modernize-billing-001.json
python3 -m harness approve modernize-billing-001 analysis <your-name>
python3 -m harness run tasks/examples/modernize-billing-001.json   # resumes
python3 -m harness status modernize-billing-001
```
