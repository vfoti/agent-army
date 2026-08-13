"""Pluggable sandbox execution layer.

The Code and Test roles run builds/tests through a SandboxExecutor. Three
backends are provided:

- LocalExecutor: runs commands in a local working directory (dev/CI default).
- DockerExecutor: runs each command in an ephemeral local Docker container
  with the task workspace bind-mounted (decision D2).
- E2BExecutor: stub for e2b.dev Firecracker microVM sandboxes.
- GitHubRunnerExecutor: stub for dispatching work to GitHub Actions runners.
"""
from __future__ import annotations

import subprocess
import re
import shutil
import sys
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


class DockerExecutor(SandboxExecutor):
    """Runs each command in an ephemeral local Docker container (decision D2).

    Model: **ephemeral container per command**. The task workspace directory
    is bind-mounted at /workspace, so state persists between commands via
    the filesystem while every command starts from a clean process
    environment. Containers run with no network by default, as
    non-root-friendly `--rm` one-shots, with CPU/memory caps.

    Real-world scenario (why ephemeral-per-command instead of one
    long-lived sandbox per task): the Test agent runs `mvn test` for a
    Spring slice, which downloads dependencies into `/workspace/.m2` (kept,
    because it's on the bind mount) but also starts a stray background
    process and pollutes env vars. With a long-lived container, the next
    command inherits that dirty state and a hung process can wedge the whole
    task. With ephemeral containers, the next command (`ng test` for the
    Angular slice) starts clean; only the workspace files carry over, and a
    runaway test is killed with its container at the timeout. The trade-off
    is a ~1s container startup per command — acceptable for build/test
    commands that run for minutes. If a mission later needs interactive,
    stateful sessions (e.g. a running dev server), add a session-scoped
    executor variant then.
    """

    def __init__(
        self,
        workdir: Path,
        image: str = "python:3.12-slim",
        network: bool = False,
        cpus: float = 2.0,
        memory: str = "2g",
    ) -> None:
        self.workdir = Path(workdir).resolve()
        self.image = image
        self.network = network
        self.cpus = cpus
        self.memory = memory

    def run(self, command: List[str], cwd: Optional[str] = None, timeout: int = 600) -> ExecResult:
        container_cwd = f"/workspace/{cwd}" if cwd else "/workspace"
        docker_cmd = [
            "docker", "run", "--rm",
            "-v", f"{self.workdir}:/workspace",
            "-w", container_cwd,
            "--cpus", str(self.cpus),
            "--memory", self.memory,
        ]
        if not self.network:
            docker_cmd += ["--network", "none"]
        docker_cmd.append(self.image)
        docker_cmd += command
        try:
            proc = subprocess.run(
                docker_cmd, capture_output=True, text=True, timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            return ExecResult(124, exc.stdout or "", f"timeout after {timeout}s")
        return ExecResult(proc.returncode, proc.stdout, proc.stderr)


class DockerSandboxExecutor(SandboxExecutor):
    """A persistent, task-scoped Docker Sandbox microVM managed by ``sbx``."""

    def __init__(
        self,
        workdir: Path,
        task_id: str,
        template: str = "shell",
        clone: bool = False,
    ) -> None:
        self.workdir = Path(workdir).resolve()
        safe_id = re.sub(r"[^a-z0-9-]+", "-", task_id.lower()).strip("-")
        self.name = f"agent-army-{safe_id or 'task'}"[:63].rstrip("-")
        self.template = template
        self.clone = clone

    def validate(self) -> None:
        if shutil.which("sbx") is None:
            raise RuntimeError("Docker Sandboxes CLI 'sbx' is not installed")
        if not self.workdir.is_dir():
            raise RuntimeError(f"sandbox workspace does not exist: {self.workdir}")

    def validate_host(self, timeout: int = 30) -> None:
        self.validate()
        if sys.platform.startswith("linux") and not Path("/dev/kvm").exists():
            raise RuntimeError("Docker Sandboxes requires KVM (/dev/kvm is unavailable)")
        result = self._invoke(["sbx", "ls"], timeout)
        if not result.ok:
            raise RuntimeError(
                "Docker Sandboxes is unavailable or unauthenticated: "
                + (result.stderr.strip() or result.stdout.strip())
            )

    @staticmethod
    def _invoke(command: List[str], timeout: int) -> ExecResult:
        try:
            proc = subprocess.run(
                command, capture_output=True, text=True, timeout=timeout,
            )
        except FileNotFoundError:
            return ExecResult(127, "", "Docker Sandboxes CLI 'sbx' is not installed")
        except subprocess.TimeoutExpired as exc:
            return ExecResult(
                124,
                exc.stdout if isinstance(exc.stdout, str) else "",
                f"timeout after {timeout}s",
            )
        return ExecResult(proc.returncode, proc.stdout, proc.stderr)

    def ensure(self, timeout: int = 120) -> ExecResult:
        """Reconnect to an existing task VM or create it if it is absent."""
        self.validate()
        probe = self._invoke(["sbx", "exec", self.name, "true"], timeout)
        if probe.ok:
            return probe
        command = ["sbx", "create", "--name", self.name]
        if self.clone:
            command.append("--clone")
        command += [self.template, str(self.workdir)]
        return self._invoke(command, timeout)

    def run(self, command: List[str], cwd: Optional[str] = None, timeout: int = 600) -> ExecResult:
        ready = self.ensure(min(timeout, 120))
        if not ready.ok:
            return ready
        sandbox_command = list(command)
        if cwd:
            target = (self.workdir / cwd).resolve()
            try:
                target.relative_to(self.workdir)
            except ValueError:
                return ExecResult(2, "", "working directory escapes sandbox workspace")
            sandbox_command = ["sh", "-lc", 'cd "$1" && shift && exec "$@"',
                               "sh", str(target), *command]
        return self._invoke(["sbx", "exec", self.name, *sandbox_command], timeout)

    def stop(self, timeout: int = 60) -> ExecResult:
        return self._invoke(["sbx", "stop", self.name], timeout)

    def remove(self, timeout: int = 60) -> ExecResult:
        return self._invoke(["sbx", "rm", "--force", self.name], timeout)


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
