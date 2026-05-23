# Shared Performance Instructions

These rules apply to all modernization agents.

1. Keep context windows small.
2. Delegate specialized work to sub-agents early instead of accumulating unrelated data in one context.
3. Keep each task scoped to one capability, module, or artifact.
4. Summarize findings in compact bullet points before handing off to the next phase.
5. Preserve source traceability (file + line references) in outputs.
6. Stop and hand off once output criteria for the current scope are met.

## Delegation policy

- If a task spans more than one domain (COBOL, SQL, Java, Angular, Spring), split it into sub-agent tasks.
- If evidence exceeds manageable size, split by bounded context or business capability.
- Prefer parallel sub-agent execution for independent analysis/build slices.
- Escalate only synthesized outputs back to the parent agent.
