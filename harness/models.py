"""Core data models for the agent army harness.

Mirrors the JSON Schemas in harness/schemas/. Kept dependency-free so the
harness can run anywhere (local, e2b, GitHub runners).
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional

ROLES = ("analysis", "design", "code", "test")

STATUS_SUCCEEDED = "succeeded"
STATUS_FAILED = "failed"
STATUS_BLOCKED = "blocked"
STATUS_AWAITING_APPROVAL = "awaiting_approval"


@dataclass
class Task:
    """Instruction intake contract (see schemas/task.schema.json)."""

    task_id: str
    source: Dict[str, str]
    goal: str
    roles: List[str]
    target: Dict[str, str] = field(default_factory=dict)
    constraints: List[str] = field(default_factory=list)
    acceptance_criteria: List[str] = field(default_factory=list)
    callback: Dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.task_id:
            raise ValueError("task_id is required")
        if not self.goal:
            raise ValueError("goal is required")
        if "system" not in self.source:
            raise ValueError("source.system is required")
        if not self.roles:
            raise ValueError("at least one role is required")
        for role in self.roles:
            if role not in ROLES:
                raise ValueError(f"unknown role: {role}")

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Task":
        return cls(
            task_id=data.get("task_id", ""),
            source=data.get("source", {}),
            goal=data.get("goal", ""),
            roles=data.get("roles", []),
            target=data.get("target", {}),
            constraints=data.get("constraints", []),
            acceptance_criteria=data.get("acceptance_criteria", []),
            callback=data.get("callback", {}),
        )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Artifact:
    type: str
    reference: str
    description: str = ""


@dataclass
class Result:
    """Result envelope emitted by each role (see schemas/result.schema.json)."""

    task_id: str
    role: str
    status: str
    artifacts: List[Artifact] = field(default_factory=list)
    open_questions: List[str] = field(default_factory=list)
    summary: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Result":
        return cls(
            task_id=data["task_id"],
            role=data["role"],
            status=data["status"],
            artifacts=[Artifact(**a) for a in data.get("artifacts", [])],
            open_questions=data.get("open_questions", []),
            summary=data.get("summary", ""),
        )


@dataclass
class AgentDefinition:
    """A role agent loaded from an agents/<role>/<role>.agent.md file."""

    name: str
    role: str
    description: str
    tools: List[str]
    inputs: Dict[str, Any]
    outputs: Dict[str, Any]
    handoff: Dict[str, Any]
    subagents: List[str]
    shared_instructions: Optional[str]
    prompt: str
