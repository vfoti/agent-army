"""Always-on runner loop (decision D4: local machine, always on).

Polls the intake source for new tasks and approvals, advances the pipeline,
and delivers results — indefinitely. Run directly or inside the provided
Docker container (D6: file-based ledger on a mounted volume).

Usage:
  python -m harness.service            # GitHub issue intake (needs env vars)
  python -m harness.service --folder   # folder intake (tasks/inbox), dry runs allowed
  python -m harness.service --once     # single poll cycle (useful for cron/tests)
"""
from __future__ import annotations

import logging
import sys
import time
from pathlib import Path
from typing import Dict

from .agent_loader import load_all_agents
from .anthropic_runner import AnthropicRoleRunner
from .budget import BudgetGuard
from .config import Config
from .intake import FolderIntake, GitHubIssueIntake, IntakeAdapter
from .ledger import TaskLedger
from .models import Task
from .orchestrator import Orchestrator
from .runners import NullRoleRunner, RoleRunner
from .sandbox import (
    DockerExecutor, DockerSandboxExecutor, LocalExecutor, SandboxExecutor,
)

log = logging.getLogger("agent-army")

REPO_ROOT = Path(__file__).resolve().parent.parent
AGENTS_DIR = REPO_ROOT / "agents"


class Service:
    def __init__(self, config: Config, intake: IntakeAdapter, dry_run: bool = False) -> None:
        self.config = config
        self.intake = intake
        data_dir = config.data_dir if config.data_dir.is_absolute() else REPO_ROOT / config.data_dir
        self.ledger = TaskLedger(data_dir / "ledger")
        self.budget = BudgetGuard(config, self.ledger)
        self._executors: Dict[str, SandboxExecutor] = {}
        if config.sandbox_backend not in {"local", "docker", "docker-sandbox"}:
            raise ValueError(f"unknown sandbox backend: {config.sandbox_backend}")
        if config.sandbox_backend == "docker-sandbox":
            DockerSandboxExecutor(
                REPO_ROOT, "startup-check", config.sandbox_template,
                config.sandbox_clone,
            ).validate_host()
        agents = load_all_agents(AGENTS_DIR)
        runners: Dict[str, RoleRunner] = {}
        for role, agent in agents.items():
            if dry_run:
                runners[role] = NullRoleRunner(agent, AGENTS_DIR)
            else:
                runners[role] = AnthropicRoleRunner(
                    agent, AGENTS_DIR, config, self.budget, REPO_ROOT,
                    self._executor_for,
                )
        self.orchestrator = Orchestrator(runners, self.ledger, self.intake)
        self.active_tasks: Dict[str, Task] = {}

    def _executor_for(self, task: Task) -> SandboxExecutor:
        existing = self._executors.get(task.task_id)
        if existing is not None:
            return existing
        if self.config.sandbox_backend == "docker-sandbox":
            executor: SandboxExecutor = DockerSandboxExecutor(
                REPO_ROOT, task.task_id, self.config.sandbox_template,
                self.config.sandbox_clone,
            )
            ready = executor.ensure(self.config.sandbox_timeout)  # type: ignore[attr-defined]
            if not ready.ok:
                self.ledger.record_sandbox(task.task_id, executor.name, "failed")  # type: ignore[attr-defined]
                raise RuntimeError(ready.stderr or ready.stdout)
            self.ledger.record_sandbox(task.task_id, executor.name, "running")  # type: ignore[attr-defined]
        elif self.config.sandbox_backend == "docker":
            executor = DockerExecutor(REPO_ROOT, image=self.config.sandbox_image)
        else:
            executor = LocalExecutor(REPO_ROOT)
        self._executors[task.task_id] = executor
        return executor

    def _cleanup_sandbox(self, task: Task) -> None:
        executor = self._executors.pop(task.task_id, None)
        if not isinstance(executor, DockerSandboxExecutor):
            return
        if self.config.sandbox_retain:
            stopped = executor.stop()
            self.ledger.record_sandbox(
                task.task_id, executor.name, "retained" if stopped.ok else "stop_failed")
        else:
            removed = executor.remove()
            self.ledger.record_sandbox(
                task.task_id, executor.name, "removed" if removed.ok else "remove_failed")

    def _sync_approvals(self, task: Task) -> None:
        if isinstance(self.intake, GitHubIssueIntake):
            for approval in self.intake.poll_approvals(task.task_id):
                if not self.ledger.is_approved(task.task_id, approval["role"]):
                    self.ledger.approve(task.task_id, approval["role"], approval["approver"])
                    log.info("approved %s/%s by %s", task.task_id,
                             approval["role"], approval["approver"])

    def cycle(self) -> None:
        """One poll cycle: ingest new tasks, sync approvals, advance all."""
        for task in self.intake.poll():
            if task.task_id not in self.active_tasks:
                log.info("ingested task %s: %s", task.task_id, task.goal)
                self.active_tasks[task.task_id] = task
        for task in list(self.active_tasks.values()):
            self._sync_approvals(task)
            results = self.orchestrator.advance(task)
            for result in results:
                log.info("[%s/%s] %s: %s", task.task_id, result.role,
                         result.status, result.summary)
            completed = {r["role"] for r in self.ledger.results_for(task.task_id)
                         if r["status"] == "succeeded"}
            if completed >= set(task.roles):
                log.info("task %s complete", task.task_id)
                self._cleanup_sandbox(task)
                del self.active_tasks[task.task_id]

    def run_forever(self) -> None:
        log.info("agent-army service started (poll every %ss)",
                 self.config.poll_interval_seconds)
        while True:
            try:
                self.cycle()
            except Exception:  # noqa: BLE001 - keep the loop alive
                log.exception("cycle failed; continuing")
            time.sleep(self.config.poll_interval_seconds)


def main(argv: list) -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    config = Config()
    use_folder = "--folder" in argv
    dry_run = "--dry-run" in argv
    if use_folder:
        data_dir = config.data_dir if config.data_dir.is_absolute() else REPO_ROOT / config.data_dir
        intake: IntakeAdapter = FolderIntake(data_dir / "inbox", data_dir / "outbox")
    else:
        if not config.github_token or not config.github_repo:
            print("Set GITHUB_TOKEN and AGENT_ARMY_REPO, or use --folder.",
                  file=sys.stderr)
            return 1
        intake = GitHubIssueIntake(config.github_repo, config.github_token,
                                   config.intake_label)
    if not dry_run and not config.anthropic_api_key:
        print("Set ANTHROPIC_API_KEY, or use --dry-run.", file=sys.stderr)
        return 1
    service = Service(config, intake, dry_run=dry_run)
    if "--once" in argv:
        service.cycle()
        return 0
    service.run_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
