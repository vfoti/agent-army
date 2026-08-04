"""Pluggable sandbox execution layer.

The Code and Test roles run builds/tests through a SandboxExecutor. Three
backends are provided:

- LocalExecutor: runs commands in a local working directory (dev/CI default).
- E2BExecutor: stub for e2b.dev Firecracker microVM sandboxes.
- GitHubRunnerExecutor: stub for dispatching work to GitHub Actions runners.
"""
from __future__ import annotations

import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional


@dataclass
class ExecResult:
    exit_code: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.exit_code == 0


class SandboxExecutor(ABC):
    @abstractmethod
    def run(self, command: List[str], cwd: Optional[str] = None, timeout: int = 600) -> ExecResult:
        """Run a command inside the sandbox and return its result."""


class LocalExecutor(SandboxExecutor):
    """Runs commands on the local machine, scoped to a working directory."""

    def __init__(self, workdir: Path) -> None:
        self.workdir = Path(workdir)

    def run(self, command: List[str], cwd: Optional[str] = None, timeout: int = 600) -> ExecResult:
        target = self.workdir / cwd if cwd else self.workdir
        proc = subprocess.run(
            command,
            cwd=str(target),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return ExecResult(proc.returncode, proc.stdout, proc.stderr)


class E2BExecutor(SandboxExecutor):
    """Stub for e2b.dev sandboxes. At deploy time, install the `e2b` SDK and
    create a sandbox from a template with the required toolchain (JDK,
    Node/Angular, COBOL tooling) preinstalled."""

    def __init__(self, template: str = "base", api_key: Optional[str] = None) -> None:
        self.template = template
        self.api_key = api_key

    def run(self, command: List[str], cwd: Optional[str] = None, timeout: int = 600) -> ExecResult:  # pragma: no cover - integration stub
        raise NotImplementedError(
            "E2BExecutor requires the e2b SDK and an API key; see docs/runtime-evaluation.md"
        )


class GitHubRunnerExecutor(SandboxExecutor):
    """Stub for GitHub Actions runners. At deploy time, dispatch a
    workflow_dispatch event and collect job results via the GitHub API."""

    def __init__(self, repo: str, workflow: str) -> None:
        self.repo = repo
        self.workflow = workflow

    def run(self, command: List[str], cwd: Optional[str] = None, timeout: int = 600) -> ExecResult:  # pragma: no cover - integration stub
        raise NotImplementedError(
            "GitHubRunnerExecutor requires GitHub API wiring; see docs/runtime-evaluation.md"
        )
