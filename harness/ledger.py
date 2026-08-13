"""Task ledger: persists per-task pipeline state so runs can pause at
governance gates (human approval) and resume later.

State is stored as one JSON file per task under a ledger directory.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from .models import Result, Task


class TaskLedger:
    def __init__(self, ledger_dir: Path) -> None:
        self.ledger_dir = Path(ledger_dir)
        self.ledger_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, task_id: str) -> Path:
        return self.ledger_dir / f"{task_id}.json"

    def load(self, task_id: str) -> Dict[str, Any]:
        path = self._path(task_id)
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        return {"task_id": task_id, "results": [], "approvals": {}, "current_role": None}

    def save(self, state: Dict[str, Any]) -> None:
        path = self._path(state["task_id"])
        path.write_text(json.dumps(state, indent=2), encoding="utf-8")

    def record_result(self, result: Result) -> None:
        state = self.load(result.task_id)
        state["results"].append(result.to_dict())
        state["current_role"] = result.role
        self.save(state)

    def approve(self, task_id: str, role: str, approver: str) -> None:
        """Record human approval for a role's gate, unblocking the next role."""
        state = self.load(task_id)
        state["approvals"][role] = {"approver": approver}
        self.save(state)

    def is_approved(self, task_id: str, role: str) -> bool:
        return role in self.load(task_id)["approvals"]

    def results_for(self, task_id: str, role: Optional[str] = None) -> List[Dict[str, Any]]:
        results = self.load(task_id)["results"]
        if role is None:
            return results
        return [r for r in results if r["role"] == role]

    def record_sandbox(self, task_id: str, name: str, status: str) -> None:
        state = self.load(task_id)
        state["sandbox"] = {"name": name, "status": status}
        self.save(state)

    def record_task(self, task: Task) -> None:
        state = self.load(task.task_id)
        state["task"] = task.to_dict()
        self.save(state)

    def pending_tasks(self) -> List[Task]:
        tasks = []
        for path in sorted(self.ledger_dir.glob("*.json")):
            state = json.loads(path.read_text(encoding="utf-8"))
            task_data = state.get("task")
            if not task_data:
                continue
            task = Task.from_dict(task_data)
            completed = {
                result["role"] for result in state.get("results", [])
                if result.get("status") == "succeeded"
            }
            if not set(task.roles) <= completed:
                tasks.append(task)
        return tasks
