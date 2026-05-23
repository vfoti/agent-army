# agent-army

A starter blueprint for a custom agent suite that modernizes legacy COBOL, SQL, and Java systems into:

- a consolidated **Angular** frontend application
- a **Spring** middleware layer

## Modernization workflow

The suite is intentionally split into separate sessions so each phase can be reviewed and approved before moving forward.

### Session 1: Source scanning and requirements discovery

**Goal:** turn legacy code into traceable requirements.

Agents in this phase:

1. **Legacy Inventory Agent**
   - Scans COBOL copybooks/programs, SQL scripts/procedures, and Java services.
   - Produces an inventory of domains, modules, interfaces, jobs, and dependencies.
2. **Behavior Extraction Agent**
   - Documents business rules, validations, calculations, and decision paths.
3. **Requirements Authoring Agent**
   - Converts findings into functional + non-functional requirements.
   - Adds traceability links back to source file and line references.

**Primary outputs:**

- `requirements/system-overview.md`
- `requirements/domain-requirements.md`
- `requirements/non-functional-requirements.md`
- `requirements/traceability-matrix.md`

### Session 2: Architecture and solution design

**Goal:** define an implementation-ready architecture based on approved requirements.

Agents in this phase:

1. **Target Architecture Agent**
   - Defines bounded contexts, service decomposition, and integration strategy.
2. **Data & API Design Agent**
   - Creates canonical data model and API contracts between Angular and Spring.
3. **Migration Planning Agent**
   - Plans incremental cutover and coexistence with legacy systems.

**Primary outputs:**

- `architecture/solution-architecture.md`
- `architecture/api-specification.md`
- `architecture/data-model.md`
- `architecture/migration-plan.md`

### Session 3: Build and delivery

**Goal:** implement approved architecture by parallel delivery teams/agents.

Agents in this phase:

1. **Angular Build Agents**
   - Implement UI modules, routing, state handling, forms, and validation.
2. **Spring Build Agents**
   - Implement domain services, orchestration, APIs, and persistence.
3. **Integration & Quality Agent**
   - Verifies cross-layer behavior, test coverage, and release readiness.

**Primary outputs:**

- frontend and middleware implementation artifacts
- automated tests and integration validation evidence
- release notes per increment

## Governance rules

- Do not begin Session 2 until Session 1 deliverables are reviewed and approved.
- Do not begin Session 3 until Session 2 architecture artifacts are reviewed and approved.
- Preserve full traceability from delivered features back to legacy source evidence.
- Prefer incremental delivery slices by business capability.

## Suggested backlog order

1. Establish repository folders for `requirements/` and `architecture/` artifacts.
2. Run Session 1 agents on a small pilot legacy domain.
3. Review and baseline requirements documents.
4. Run Session 2 architecture agents.
5. Approve architecture baseline.
6. Start Session 3 build agents by feature slice.
