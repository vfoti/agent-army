"""Ingestion adapters: normalize external instructions into Task envelopes.

All adapters implement IntakeAdapter. The folder adapter is fully functional
for local runs; GitHub-issue and webhook adapters are thin stubs showing how
other sources plug in behind the same interface.
"""
from __future__ import annotations

import json
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, List, Optional

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
    """GitHub issue intake (decision D3).

    Chosen sub-decision conventions:
    - **Trigger label:** issues labeled `agent-task` (configurable) become
      tasks. The task envelope is read from a ```json fenced block in the
      issue body if present; otherwise a default envelope is built from the
      issue title (goal) and all four roles.
    - **Approvals:** a maintainer comments `/approve <role>` on the issue;
      the always-on loop records it in the ledger and resumes the pipeline.
    - **Results:** each role's result envelope is posted back as an issue
      comment; PRs from the Code agent are linked in artifacts.
    - **De-duplication:** the label is swapped to `agent-task:accepted`
      after intake so an issue is only ingested once.

    Uses only urllib (no SDK). Requires GITHUB_TOKEN with repo scope.
    """

    ACCEPTED_LABEL_SUFFIX = ":accepted"

    def __init__(self, repo: str, token: str, label: str = "agent-task") -> None:
        self.repo = repo
        self.token = token
        self.label = label
        self.api = f"https://api.github.com/repos/{repo}"

    # -- HTTP helpers -------------------------------------------------

    def _request(self, method: str, url: str, body: Optional[dict] = None) -> Any:
        import urllib.request

        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers={
            "Authorization": "Bearer " + self.token,
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
        })
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read().decode() or "null")

    # -- Task construction --------------------------------------------

    @staticmethod
    def _envelope_from_body(body: str) -> Optional[dict]:
        """Extract a ```json fenced task envelope from an issue body."""
        if not body or "```json" not in body:
            return None
        fragment = body.split("```json", 1)[1].split("```", 1)[0]
        try:
            return json.loads(fragment)
        except json.JSONDecodeError:
            return None

    def _task_from_issue(self, issue: dict) -> Task:
        envelope = self._envelope_from_body(issue.get("body") or "")
        if envelope is None:
            envelope = {
                "goal": issue["title"],
                "roles": ["analysis", "design", "code", "test"],
            }
        envelope.setdefault("task_id", f"issue-{issue['number']}")
        envelope["source"] = {"system": "github-issue", "reference": issue["html_url"]}
        envelope.setdefault("callback", {
            "type": "github-issue", "destination": str(issue["number"]),
        })
        return Task.from_dict(envelope)

    # -- IntakeAdapter interface --------------------------------------

    def poll(self) -> List[Task]:
        issues = self._request(
            "GET", f"{self.api}/issues?labels={self.label}&state=open")
        tasks = []
        for issue in issues:
            if "pull_request" in issue:
                continue
            tasks.append(self._task_from_issue(issue))
            number = issue["number"]
            self._request(
                "DELETE", f"{self.api}/issues/{number}/labels/{self.label}")
            self._request("POST", f"{self.api}/issues/{number}/labels", {
                "labels": [self.label + self.ACCEPTED_LABEL_SUFFIX]})
        return tasks

    def deliver(self, result: Result) -> None:
        issue_number = result.task_id.removeprefix("issue-")
        lines = [f"## [{result.role}] {result.status}", "", result.summary or ""]
        if result.artifacts:
            lines += ["", "**Artifacts:**"]
            lines += [f"- {a.type}: {a.reference} {a.description}".rstrip()
                      for a in result.artifacts]
        if result.open_questions:
            lines += ["", "**Open questions:**"]
            lines += [f"- {q}" for q in result.open_questions]
        if result.status == "awaiting_approval":
            lines += ["", f"Reply `/approve {result.role}` to continue."]
        self._request("POST", f"{self.api}/issues/{issue_number}/comments",
                      {"body": "\n".join(lines)})

    def poll_approvals(self, task_id: str) -> List[Dict[str, str]]:
        """Scan issue comments for `/approve <role>` commands.

        Returns [{"role": ..., "approver": ...}] for each command found.
        The caller (runner loop) is responsible for recording approvals in
        the ledger idempotently.
        """
        issue_number = task_id.removeprefix("issue-")
        comments = self._request(
            "GET", f"{self.api}/issues/{issue_number}/comments")
        approvals = []
        for comment in comments:
            for line in (comment.get("body") or "").splitlines():
                line = line.strip()
                if line.startswith("/approve "):
                    approvals.append({
                        "role": line.split(maxsplit=1)[1].strip(),
                        "approver": comment["user"]["login"],
                    })
        return approvals


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
