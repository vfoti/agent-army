"""Role runners: bind an AgentDefinition to an execution backend.

RoleRunner is the interface the orchestrator drives. The default
PromptRoleRunner does not call an LLM itself — it prepares the composed
system prompt and task context, then delegates to a `invoke` callable so any
backend (LangChain/deepagents, Copilot coding agent, direct API) can be
plugged in. A NullRoleRunner is provided for dry runs and tests.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .agent_loader import build_system_prompt
from .models import AgentDefinition, Artifact, Result, Task, STATUS_SUCCEEDED


class RoleRunner(ABC):
    """Executes one role of the pipeline for a task."""

    def __init__(self, agent: AgentDefinition, agents_dir: Path) -> None:
        self.agent = agent
        self.agents_dir = agents_dir

    @abstractmethod
    def run(self, task: Task, prior_results: List[Dict[str, Any]]) -> Result:
        """Run this role for the task, given prior roles' result envelopes."""


class PromptRoleRunner(RoleRunner):
    """Composes the role prompt + task context and delegates to a pluggable
    `invoke(system_prompt, user_prompt) -> dict` callable that returns a
    result-envelope-shaped dict."""

    def __init__(
        self,
        agent: AgentDefinition,
        agents_dir: Path,
        invoke: Callable[[str, str], Dict[str, Any]],
    ) -> None:
        super().__init__(agent, agents_dir)
        self.invoke = invoke

    def build_user_prompt(self, task: Task, prior_results: List[Dict[str, Any]]) -> str:
        lines = [
            f"Task ID: {task.task_id}",
            f"Goal: {task.goal}",
        ]
        if task.constraints:
            lines.append("Constraints:")
            lines.extend(f"- {c}" for c in task.constraints)
        if task.acceptance_criteria:
            lines.append("Acceptance criteria:")
            lines.extend(f"- {a}" for a in task.acceptance_criteria)
        if prior_results:
            lines.append("Prior role results:")
            for r in prior_results:
                lines.append(f"- [{r['role']}] {r.get('summary', '')}")
                for artifact in r.get("artifacts", []):
                    lines.append(f"  - {artifact['type']}: {artifact['reference']}")
        return "\n".join(lines)

    def run(self, task: Task, prior_results: List[Dict[str, Any]]) -> Result:
        system_prompt = build_system_prompt(self.agent, self.agents_dir)
        user_prompt = self.build_user_prompt(task, prior_results)
        raw = self.invoke(system_prompt, user_prompt)
        raw.setdefault("task_id", task.task_id)
        raw.setdefault("role", self.agent.role)
        return Result.from_dict(raw)


class NullRoleRunner(RoleRunner):
    """Dry-run runner: succeeds immediately without doing work. Useful for
    testing the orchestration/gating logic."""

    def run(self, task: Task, prior_results: List[Dict[str, Any]]) -> Result:
        return Result(
            task_id=task.task_id,
            role=self.agent.role,
            status=STATUS_SUCCEEDED,
            artifacts=[
                Artifact(type="report", reference=f"dry-run/{self.agent.role}.md",
                         description="dry run placeholder")
            ],
            summary=f"Dry run of {self.agent.name} completed.",
        )
