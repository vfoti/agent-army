"""Repeatable workflow definitions for the three modernization sessions."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Workflow:
    name: str
    session: int
    agent: str
    outputs: tuple[str, ...]
    completion: tuple[str, ...]


WORKFLOWS = (
    Workflow("discovery", 1, "requirements-discovery", (
        "requirements/system-overview.md", "requirements/domain-requirements.md",
        "requirements/non-functional-requirements.md", "requirements/traceability-matrix.md",
    ), ("requirements are atomic and testable", "every requirement has source references")),
    Workflow("design", 2, "architecture-design", (
        "architecture/solution-architecture.md", "architecture/api-specification.md",
        "architecture/data-model.md", "architecture/migration-plan.md",
    ), ("architecture decisions link to requirement IDs", "artifacts are approval-ready")),
    Workflow("delivery", 3, "build-delivery", (
        "frontend and middleware increments", "automated test evidence", "release notes",
    ), ("tests pass", "traceability is preserved", "release readiness is documented")),
)
