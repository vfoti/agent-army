"""Core contracts and runtime for the repository's coding-agent harness.

The model adapter is deliberately a protocol: providers can be added without
letting provider-specific code bypass the tool and approval policy.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Protocol


class HarnessError(RuntimeError):
    """Base class for expected harness failures."""


class ApprovalRequired(HarnessError):
    """Raised when a requested operation needs explicit human approval."""


class ModelAdapter(Protocol):
    def complete(self, messages: list[dict[str, str]], tools: list[str]) -> dict[str, Any]:
        """Return ``{"content": str, "tool_calls": [...]}``."""


@dataclass
class RunRequest:
    task: str
    agent: str = "requirements-discovery"
    session: int = 1
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    approval_token: str | None = None
    max_turns: int = 20
    retry_limit: int = 2


@dataclass
class RunResult:
    run_id: str
    status: str
    content: str = ""
    turns: int = 0
    artifacts: list[str] = field(default_factory=list)
    error: str | None = None


class CancellationToken:
    def __init__(self) -> None:
        self.cancelled = False

    def cancel(self) -> None:
        self.cancelled = True

    def raise_if_cancelled(self) -> None:
        if self.cancelled:
            raise HarnessError("run cancelled")


class JsonlStore:
    """Append-only persistence for resumable runs and audit events."""

    def __init__(self, root: Path) -> None:
        self.path = root / ".harness" / "events.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, event: str, run_id: str, **data: Any) -> None:
        record = {"timestamp": time.time(), "event": event, "run_id": run_id, **data}
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, sort_keys=True) + "\n")

    def events(self, run_id: str) -> Iterable[dict[str, Any]]:
        if not self.path.exists():
            return ()
        with self.path.open(encoding="utf-8") as stream:
            return (json.loads(line) for line in stream if json.loads(line)["run_id"] == run_id)


@dataclass
class ToolPolicy:
    workspace: Path
    allowed_commands: tuple[str, ...] = ("git", "python", "pytest", "npm", "pnpm", "mvn")
    approval_commands: tuple[str, ...] = ("git commit", "git push", "rm", "npm publish", "pnpm publish")
    max_output: int = 20_000

    def path(self, value: str) -> Path:
        candidate = (self.workspace / value).resolve()
        if candidate != self.workspace and self.workspace not in candidate.parents:
            raise HarnessError(f"path escapes workspace: {value}")
        return candidate

    def command(self, command: str, approved: bool = False) -> list[str]:
        parts = shlex.split(command)
        if not parts or parts[0] not in self.allowed_commands:
            raise HarnessError(f"command is not allowed: {parts[0] if parts else '<empty>'}")
        normalized = " ".join(parts[:2])
        if any(normalized.startswith(prefix) for prefix in self.approval_commands) and not approved:
            raise ApprovalRequired(f"approval required for: {normalized}")
        return parts


class WorkspaceTools:
    def __init__(self, policy: ToolPolicy) -> None:
        self.policy = policy

    def read(self, path: str) -> str:
        return self.policy.path(path).read_text(encoding="utf-8")[: self.policy.max_output]

    def search(self, pattern: str, path: str = ".") -> list[str]:
        root = self.policy.path(path)
        regex = re.compile(pattern)
        matches: list[str] = []
        for file in root.rglob("*"):
            if file.is_file() and ".git" not in file.parts and regex.search(file.read_text(encoding="utf-8", errors="ignore")):
                matches.append(str(file.relative_to(self.policy.workspace)))
        return matches

    def write(self, path: str, content: str, approved: bool = False) -> str:
        target = self.policy.path(path)
        if target.exists() and not approved:
            raise ApprovalRequired(f"approval required to overwrite: {path}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return str(target.relative_to(self.policy.workspace))

    def shell(self, command: str, approved: bool = False, timeout: int = 120) -> str:
        parts = self.policy.command(command, approved)
        result = subprocess.run(
            parts, cwd=self.policy.workspace, capture_output=True, text=True,
            timeout=timeout, check=False,
        )
        output = (result.stdout + result.stderr)[-self.policy.max_output :]
        if result.returncode:
            raise HarnessError(f"command failed ({result.returncode}): {output}")
        return output


@dataclass
class AgentDefinition:
    name: str
    path: str
    purpose: str
    instructions: str


class AgentRegistry:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.agents: dict[str, AgentDefinition] = {}
        self.subagents: dict[str, str] = {}
        self.skills: dict[str, str] = {}
        self.shared_instructions = ""
        self.reload()

    def reload(self) -> None:
        agent_dir = self.root / "agents"
        for path in agent_dir.glob("*.agent.md"):
            text = path.read_text(encoding="utf-8")
            title = re.search(r"^# Agent: (.+)$", text, re.MULTILINE)
            purpose = re.search(r"^## Purpose\s*\n(.+)$", text, re.MULTILINE)
            name = path.stem.removesuffix(".agent")
            self.agents[name] = AgentDefinition(
                name=name, path=str(path.relative_to(self.root)),
                purpose=purpose.group(1).strip() if purpose else "",
                instructions=text,
            )
        for path in (agent_dir / "subagents").glob("*.subagent.md"):
            self.subagents[path.stem.removesuffix(".subagent")] = path.read_text(encoding="utf-8")
        shared = agent_dir / "shared-performance.instructions.md"
        if shared.exists():
            self.shared_instructions = shared.read_text(encoding="utf-8")
        for path in (agent_dir / "skills").glob("*/SKILL.md") if (agent_dir / "skills").exists() else ():
            self.skills[path.parent.name] = path.read_text(encoding="utf-8")

    def context(self, agent: str, task: str, session: int, skill: str | None = None) -> list[dict[str, str]]:
        if agent not in self.agents:
            raise HarnessError(f"unknown agent: {agent}")
        definition = self.agents[agent]
        content = f"{self.shared_instructions}\n\n{definition.instructions}"
        if skill:
            if skill not in self.skills:
                raise HarnessError(f"unknown skill: {skill}")
            content += f"\n\nRelevant skill:\n{self.skills[skill]}"
        return [{"role": "system", "content": content}, {
            "role": "user", "content": f"Session {session}. Task: {task}",
        }]


class AgentHarness:
    """Runs a model adapter while retaining control of all side effects."""

    def __init__(self, root: str | Path, model: ModelAdapter, store: JsonlStore | None = None) -> None:
        self.root = Path(root).resolve()
        self.model = model
        self.registry = AgentRegistry(self.root)
        self.policy = ToolPolicy(self.root)
        self.tools = WorkspaceTools(self.policy)
        self.store = store or JsonlStore(self.root)

    def _check_phase(self, request: RunRequest) -> None:
        if request.session not in (1, 2, 3):
            raise HarnessError("session must be 1, 2, or 3")
        if request.session > 1:
            approvals = list(self.store.events(request.run_id))
            required = f"session-{request.session - 1}-approved"
            if not any(event["event"] == required for event in approvals):
                raise ApprovalRequired(f"previous phase is not approved: {required}")

    def approve(self, run_id: str, session: int) -> None:
        if session not in (1, 2):
            raise HarnessError("only Sessions 1 and 2 require approval gates")
        self.store.append(f"session-{session}-approved", run_id)

    def run(self, request: RunRequest, cancel: CancellationToken | None = None) -> RunResult:
        cancel = cancel or CancellationToken()
        self._check_phase(request)
        self.store.append("run-started", request.run_id, request=asdict(request))
        messages = self.registry.context(request.agent, request.task, request.session)
        last_content = ""
        try:
            for turn in range(1, request.max_turns + 1):
                cancel.raise_if_cancelled()
                response = None
                for attempt in range(request.retry_limit + 1):
                    try:
                        response = self.model.complete(messages, ["read", "search", "write", "shell"])
                        break
                    except Exception as error:
                        self.store.append("model-retry", request.run_id, turn=turn, attempt=attempt + 1, error=str(error))
                        if attempt == request.retry_limit:
                            raise
                # Retain the system contract and recent turns when a long task
                # would otherwise grow without bound.
                if len(messages) > 12:
                    messages = [messages[0], *messages[-10:]]
                    self.store.append("context-compacted", request.run_id, turn=turn)
                assert response is not None
                last_content = str(response.get("content", ""))
                self.store.append("model-response", request.run_id, turn=turn, response=response)
                messages.append({"role": "assistant", "content": last_content})
                calls = response.get("tool_calls", [])
                if not calls:
                    self.store.append("run-completed", request.run_id, turns=turn)
                    return RunResult(request.run_id, "completed", last_content, turn)
                for call in calls:
                    cancel.raise_if_cancelled()
                    self.store.append("tool-requested", request.run_id, turn=turn, tool=call.get("name"))
                    result = self._tool(call, request.approval_token)
                    messages.append({"role": "tool", "content": result})
            raise HarnessError("maximum turns exceeded")
        except Exception as error:
            self.store.append("run-failed", request.run_id, error=str(error))
            return RunResult(request.run_id, "failed", last_content, error=str(error))

    def _tool(self, call: dict[str, Any], approval_token: str | None) -> str:
        name, arguments = call.get("name"), call.get("arguments", {})
        approved = approval_token == os.environ.get("HARNESS_APPROVAL_TOKEN") and bool(approval_token)
        if name == "read":
            return self.tools.read(arguments["path"])
        if name == "search":
            return json.dumps(self.tools.search(arguments["pattern"], arguments.get("path", ".")))
        if name == "write":
            return self.tools.write(arguments["path"], arguments["content"], approved)
        if name == "shell":
            return self.tools.shell(arguments["command"], approved)
        raise HarnessError(f"unknown tool: {name}")
