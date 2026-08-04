"""Budget guards (decision D5): hard token/cost ceilings per role and per
task, enforced before and after every model call. Usage is persisted in the
task ledger so ceilings survive restarts of the always-on runner.
"""
from __future__ import annotations

from typing import Any, Dict

from .config import Config
from .ledger import TaskLedger


class BudgetExceeded(RuntimeError):
    """Raised when a model call would exceed a configured ceiling."""


class BudgetGuard:
    def __init__(self, config: Config, ledger: TaskLedger) -> None:
        self.config = config
        self.ledger = ledger

    def _usage(self, task_id: str) -> Dict[str, Any]:
        state = self.ledger.load(task_id)
        return state.setdefault("usage", {"roles": {}, "total_tokens": 0, "total_usd": 0.0})

    def cost_usd(self, input_tokens: int, output_tokens: int) -> float:
        return (
            input_tokens * self.config.price_per_mtok_input
            + output_tokens * self.config.price_per_mtok_output
        ) / 1_000_000

    def check(self, task_id: str, role: str) -> None:
        """Raise BudgetExceeded if the task/role has already hit a ceiling."""
        usage = self._usage(task_id)
        if usage["total_tokens"] >= self.config.budget_tokens_per_task:
            raise BudgetExceeded(
                f"task {task_id}: token ceiling reached "
                f"({usage['total_tokens']}/{self.config.budget_tokens_per_task})")
        if usage["total_usd"] >= self.config.budget_usd_per_task:
            raise BudgetExceeded(
                f"task {task_id}: cost ceiling reached "
                f"(${usage['total_usd']:.2f}/${self.config.budget_usd_per_task:.2f})")
        role_usd = usage["roles"].get(role, {}).get("usd", 0.0)
        if role_usd >= self.config.budget_usd_per_role:
            raise BudgetExceeded(
                f"task {task_id} role {role}: role cost ceiling reached "
                f"(${role_usd:.2f}/${self.config.budget_usd_per_role:.2f})")

    def record(self, task_id: str, role: str, input_tokens: int, output_tokens: int) -> None:
        """Record usage from a completed model call into the ledger."""
        state = self.ledger.load(task_id)
        usage = state.setdefault("usage", {"roles": {}, "total_tokens": 0, "total_usd": 0.0})
        usd = self.cost_usd(input_tokens, output_tokens)
        tokens = input_tokens + output_tokens
        role_usage = usage["roles"].setdefault(role, {"tokens": 0, "usd": 0.0})
        role_usage["tokens"] += tokens
        role_usage["usd"] += usd
        usage["total_tokens"] += tokens
        usage["total_usd"] += usd
        self.ledger.save(state)
