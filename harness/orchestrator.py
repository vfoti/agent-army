"""Pipeline orchestrator with human governance gates.

Drives a task through its requested roles in order (analysis → design →
code → test). After each gated role completes, the pipeline pauses with
STATUS_AWAITING_APPROVAL until a human approval is recorded in the ledger,
mirroring the governance rules in the top-level README.

This is a dependency-free state machine; it maps 1:1 onto a LangGraph graph
(roles = nodes, gates = interrupts) if/when the LangChain deployment mode is
adopted.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Set

from .intake import IntakeAdapter
from .ledger import TaskLedger
from .models import Result, Task, STATUS_AWAITING_APPROVAL, STATUS_SUCCEEDED
from .runners import RoleRunner

# Roles whose completion requires human approval before the next role starts.
DEFAULT_GATED_ROLES: Set[str] = {"analysis", "design", "test"}


class Orchestrator:
    def __init__(
        self,
        runners: Dict[str, RoleRunner],
        ledger: TaskLedger,
        intake: Optional[IntakeAdapter] = None,
        gated_roles: Optional[Set[str]] = None,
    ) -> None:
        self.runners = runners
        self.ledger = ledger
        self.intake = intake
        self.gated_roles = DEFAULT_GATED_ROLES if gated_roles is None else gated_roles

    def _deliver(self, result: Result) -> None:
        self.ledger.record_result(result)
        if self.intake is not None:
            self.intake.deliver(result)

    def _completed_roles(self, task: Task) -> Set[str]:
        return {
            r["role"]
            for r in self.ledger.results_for(task.task_id)
            if r["status"] == STATUS_SUCCEEDED
        }

    def advance(self, task: Task) -> List[Result]:
        """Run as many pending roles as possible for the task, stopping at
        the first unapproved governance gate. Returns results produced in
        this call. Safe to call repeatedly to resume after approvals."""
        produced: List[Result] = []
        completed = self._completed_roles(task)
        prior = self.ledger.results_for(task.task_id)
        previous_role: Optional[str] = None
        for role in task.roles:
            if role in completed:
                previous_role = role
                continue
            if previous_role is not None and previous_role in self.gated_roles:
                if not self.ledger.is_approved(task.task_id, previous_role):
                    gate = Result(
                        task_id=task.task_id,
                        role=previous_role,
                        status=STATUS_AWAITING_APPROVAL,
                        summary=f"Awaiting human approval of {previous_role} before {role}.",
                    )
                    produced.append(gate)
                    if self.intake is not None:
                        self.intake.deliver(gate)
                    return produced
            runner = self.runners.get(role)
            if runner is None:
                raise ValueError(f"no runner configured for role {role!r}")
            result = runner.run(task, prior)
            self._deliver(result)
            produced.append(result)
            prior = self.ledger.results_for(task.task_id)
            if result.status != STATUS_SUCCEEDED:
                return produced
            previous_role = role
        return produced

    def approve(self, task_id: str, role: str, approver: str) -> None:
        """Record a human approval, unblocking the gate after `role`."""
        self.ledger.approve(task_id, role, approver)
