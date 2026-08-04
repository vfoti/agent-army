"""CLI for the agent army harness.

Usage:
  python -m harness run <task.json>        Run/resume a task (dry-run runners)
  python -m harness approve <task_id> <role> <approver>
  python -m harness status <task_id>
  python -m harness agents                  List loaded agent definitions
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from .agent_loader import load_all_agents
from .intake import FolderIntake
from .ledger import TaskLedger
from .models import Task
from .orchestrator import Orchestrator
from .runners import NullRoleRunner

REPO_ROOT = Path(__file__).resolve().parent.parent
AGENTS_DIR = REPO_ROOT / "agents"
TASKS_DIR = REPO_ROOT / "tasks"


def _build_orchestrator() -> Orchestrator:
    agents = load_all_agents(AGENTS_DIR)
    runners = {role: NullRoleRunner(agent, AGENTS_DIR) for role, agent in agents.items()}
    ledger = TaskLedger(TASKS_DIR / "ledger")
    intake = FolderIntake(TASKS_DIR / "inbox", TASKS_DIR / "outbox")
    return Orchestrator(runners, ledger, intake)


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 1
    command, *args = argv
    if command == "agents":
        for role, agent in load_all_agents(AGENTS_DIR).items():
            print(f"{role}: {agent.name} — {agent.description}")
        return 0
    orchestrator = _build_orchestrator()
    if command == "run":
        task = Task.from_dict(json.loads(Path(args[0]).read_text(encoding="utf-8")))
        for result in orchestrator.advance(task):
            print(f"[{result.role}] {result.status}: {result.summary}")
        return 0
    if command == "approve":
        task_id, role, approver = args
        orchestrator.approve(task_id, role, approver)
        print(f"approved {role} for {task_id} by {approver}")
        return 0
    if command == "status":
        state = orchestrator.ledger.load(args[0])
        print(json.dumps(state, indent=2))
        return 0
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
