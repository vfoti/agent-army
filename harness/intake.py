"""Ingestion adapters: normalize external instructions into Task envelopes.

All adapters implement IntakeAdapter. The folder adapter is fully functional
for local runs; GitHub-issue and webhook adapters are thin stubs showing how
other sources plug in behind the same interface.
"""
from __future__ import annotations

import json
from abc import ABC, abstractmethod
from pathlib import Path
from typing import List

from .models import Task, Result


class IntakeAdapter(ABC):
    """Normalizes instructions from an external source into Task envelopes
    and delivers Result envelopes back to that source."""

    @abstractmethod
    def poll(self) -> List[Task]:
        """Return new tasks from the source."""

    @abstractmethod
    def deliver(self, result: Result) -> None:
        """Deliver a result envelope back to the source."""


class FolderIntake(IntakeAdapter):
    """Reads task JSON files from a folder (e.g. tasks/inbox) and writes
    result envelopes to an output folder (e.g. tasks/outbox)."""

    def __init__(self, inbox: Path, outbox: Path) -> None:
        self.inbox = Path(inbox)
        self.outbox = Path(outbox)
        self.outbox.mkdir(parents=True, exist_ok=True)

    def poll(self) -> List[Task]:
        tasks = []
        if not self.inbox.exists():
            return tasks
        for path in sorted(self.inbox.glob("*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            tasks.append(Task.from_dict(data))
        return tasks

    def deliver(self, result: Result) -> None:
        path = self.outbox / f"{result.task_id}.{result.role}.result.json"
        path.write_text(json.dumps(result.to_dict(), indent=2), encoding="utf-8")


class GitHubIssueIntake(IntakeAdapter):
    """Stub: turn labeled GitHub issues into tasks and post results as
    issue comments. Requires a GitHub token/webhook wiring at deploy time."""

    def __init__(self, repo: str) -> None:
        self.repo = repo

    def poll(self) -> List[Task]:  # pragma: no cover - integration stub
        raise NotImplementedError(
            "GitHubIssueIntake requires GitHub API wiring; see docs/harness.md"
        )

    def deliver(self, result: Result) -> None:  # pragma: no cover - integration stub
        raise NotImplementedError


class WebhookIntake(IntakeAdapter):
    """Stub: accept tasks via an HTTP endpoint and deliver results to a
    callback URL. Host inside any WSGI/ASGI app at deploy time."""

    def __init__(self, callback_url: str) -> None:
        self.callback_url = callback_url

    def poll(self) -> List[Task]:  # pragma: no cover - integration stub
        raise NotImplementedError(
            "WebhookIntake requires an HTTP server; see docs/harness.md"
        )

    def deliver(self, result: Result) -> None:  # pragma: no cover - integration stub
        raise NotImplementedError
