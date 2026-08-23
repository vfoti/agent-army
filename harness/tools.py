"""Role-scoped tool execution with the harness security guards.

`RoleToolbox` owns every guard that must hold no matter which agent
framework drives the model:

- the per-role tool allow-list taken from the agent definition frontmatter,
- workspace path confinement for every file argument,
- argv-only sandbox execution (never a host shell),
- a per-run tool-call cap and an output-size cap,
- redaction of configured API tokens from tool output,
- read-only SQL enforcement for the DB2/PostgreSQL query tools.

Both `AnthropicRoleRunner` and `DeepAgentsRoleRunner` execute tools through
this class, so adopting a third-party agent harness cannot silently inherit
that framework's more permissive defaults.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .config import Config
from .models import AgentDefinition, Task
from .sandbox import SandboxExecutor

# JSON Schema descriptors for every tool the harness can expose. Roles only
# receive the subset named in their agent definition's `tools:` list.
TOOL_SCHEMAS: Dict[str, Dict[str, Any]] = {
    "repo_read": {
        "description": "Read a UTF-8 file in the task workspace.",
        "input_schema": {"type": "object", "properties": {
            "path": {"type": "string"}}, "required": ["path"]},
    },
    "repo_write": {
        "description": "Write a UTF-8 file in the task workspace.",
        "input_schema": {"type": "object", "properties": {
            "path": {"type": "string"}, "content": {"type": "string"}},
            "required": ["path", "content"]},
    },
    "doc_write": {
        "description": "Write a UTF-8 document in the task workspace.",
        "input_schema": {"type": "object", "properties": {
            "path": {"type": "string"}, "content": {"type": "string"}},
            "required": ["path", "content"]},
    },
    "sandbox_exec": {
        "description": "Run an argv command in the configured sandbox.",
        "input_schema": {"type": "object", "properties": {
            "command": {"type": "array", "items": {"type": "string"}, "minItems": 1},
            "cwd": {"type": "string"},
            "timeout": {"type": "integer", "minimum": 1}},
            "required": ["command"]},
    },
    "git": {
        "description": "Run a git command in the configured sandbox.",
        "input_schema": {"type": "object", "properties": {
            "args": {"type": "array", "items": {"type": "string"}},
            "cwd": {"type": "string"}}, "required": ["args"]},
    },
    "database_query": {
        "description": (
            "Run one read-only SQL query against the configured DB2 source "
            "or PostgreSQL target."
        ),
        "input_schema": {"type": "object", "properties": {
            "database": {"type": "string", "enum": ["db2", "postgres"]},
            "sql": {"type": "string"},
            "timeout": {"type": "integer", "minimum": 1}},
            "required": ["database", "sql"]},
    },
    "database_schema": {
        "description": (
            "Extract DDL metadata from the configured DB2 source or "
            "PostgreSQL target."
        ),
        "input_schema": {"type": "object", "properties": {
            "database": {"type": "string", "enum": ["db2", "postgres"]},
            "timeout": {"type": "integer", "minimum": 1}},
            "required": ["database"]},
    },
    "database_migrate": {
        "description": (
            "Apply a workspace SQL migration file transactionally to the "
            "configured PostgreSQL target."
        ),
        "input_schema": {"type": "object", "properties": {
            "path": {"type": "string"},
            "timeout": {"type": "integer", "minimum": 1}},
            "required": ["path"]},
    },
}

SANDBOX_TOOLS = frozenset({
    "sandbox_exec", "git", "database_query", "database_schema", "database_migrate",
})


class ToolCallLimitExceeded(RuntimeError):
    """Raised when a role exceeds its configured tool-call budget."""


class RoleToolbox:
    """Executes the tools a role is allowed to use, under the harness guards."""

    def __init__(
        self,
        agent: AgentDefinition,
        config: Config,
        workspace: Optional[Path] = None,
        executor_factory: Optional[Callable[[Task], SandboxExecutor]] = None,
    ) -> None:
        self.agent = agent
        self.config = config
        self.workspace = (workspace or Path.cwd()).resolve()
        self.executor_factory = executor_factory
        self.calls = 0

    @property
    def allowed_tools(self) -> List[str]:
        """Tool names this role may use, in agent-definition order."""
        return [name for name in self.agent.tools if name in TOOL_SCHEMAS]

    def reset_calls(self) -> None:
        """Reset the per-run tool-call counter (call once per role run)."""
        self.calls = 0

    def anthropic_tool_definitions(self) -> List[Dict[str, Any]]:
        """Allow-listed tools in Anthropic Messages API tool format."""
        return [
            {"name": name, **TOOL_SCHEMAS[name]}
            for name in self.allowed_tools
        ]

    def workspace_path(self, raw: str) -> Path:
        path = (self.workspace / raw).resolve()
        try:
            path.relative_to(self.workspace)
        except ValueError as exc:
            raise ValueError("path escapes task workspace") from exc
        return path

    def redact(self, text: str) -> str:
        for secret in (self.config.anthropic_api_key, self.config.github_token):
            if secret:
                text = text.replace(secret, "[REDACTED]")
        return text[:self.config.sandbox_max_output_chars]

    @staticmethod
    def read_only_sql(sql: str) -> str:
        cleaned = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
        cleaned = re.sub(r"--[^\n]*", " ", cleaned).strip()
        if not cleaned:
            raise ValueError("SQL query is empty")
        statement = cleaned[:-1].rstrip() if cleaned.endswith(";") else cleaned
        if ";" in statement:
            raise ValueError("database_query accepts exactly one SQL statement")
        without_strings = re.sub(r"'(?:''|[^'])*'", "''", statement)
        first = without_strings.split(None, 1)[0].upper()
        if first not in {"SELECT", "WITH", "VALUES", "EXPLAIN"}:
            raise ValueError("database_query only accepts read-only SQL")
        prohibited = (
            r"\b(INSERT|UPDATE|DELETE|MERGE|CALL|CREATE|ALTER|DROP|TRUNCATE|"
            r"GRANT|REVOKE|RENAME|COMMENT|SET)\b"
        )
        if re.search(prohibited, without_strings, flags=re.IGNORECASE):
            raise ValueError("database_query only accepts read-only SQL")
        return statement

    def database_command(self, name: str, arguments: Dict[str, Any]) -> List[str]:
        if name == "database_migrate":
            if not self.config.postgres_service:
                raise RuntimeError("AGENT_ARMY_POSTGRES_SERVICE is not configured")
            raw_path = str(arguments["path"])
            path = self.workspace_path(raw_path)
            if path.suffix.lower() != ".sql" or not path.is_file():
                raise ValueError("database_migrate path must be an existing .sql file")
            relative = path.relative_to(self.workspace).as_posix()
            return [
                "psql", f"service={self.config.postgres_service}", "--no-psqlrc",
                "--set=ON_ERROR_STOP=1", "--single-transaction", "--file", relative,
            ]

        database = str(arguments["database"])
        if database not in {"db2", "postgres"}:
            raise ValueError("database must be 'db2' or 'postgres'")
        if database == "postgres":
            if not self.config.postgres_service:
                raise RuntimeError("AGENT_ARMY_POSTGRES_SERVICE is not configured")
            connection = f"service={self.config.postgres_service}"
            if name == "database_schema":
                return [
                    "pg_dump", f"--dbname={connection}", "--schema-only",
                    "--no-owner", "--no-privileges",
                ]
            sql = self.read_only_sql(str(arguments["sql"]))
            return [
                "psql", connection, "--no-psqlrc", "--set=ON_ERROR_STOP=1",
                "--csv", "--command", f"BEGIN READ ONLY; {sql}; COMMIT;",
            ]

        if not self.config.db2_database:
            raise RuntimeError("AGENT_ARMY_DB2_DATABASE is not configured")
        if name == "database_schema":
            return ["db2look", "-d", self.config.db2_database, "-e", "-x"]
        sql = self.read_only_sql(str(arguments["sql"]))
        return [
            "db2", "-x", "-td;",
            f"connect to {self.config.db2_database}; {sql}; connect reset;",
        ]

    def count_call(self) -> None:
        """Charge one call against the per-run tool-call cap."""
        self.calls += 1
        if self.calls > self.config.sandbox_max_tool_calls:
            raise ToolCallLimitExceeded("maximum tool-call count exceeded")

    def execute(self, task: Task, name: str, arguments: Dict[str, Any]) -> str:
        """Run one allow-listed tool and return its redacted, capped output."""
        if name not in self.agent.tools:
            raise ValueError(f"tool {name!r} is not allowed for role {self.agent.role}")
        if name == "repo_read":
            return self.redact(
                self.workspace_path(str(arguments["path"])).read_text(encoding="utf-8"))
        if name in {"repo_write", "doc_write"}:
            path = self.workspace_path(str(arguments["path"]))
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(str(arguments["content"]), encoding="utf-8")
            return f"wrote {path.relative_to(self.workspace)}"
        if name in SANDBOX_TOOLS:
            if self.executor_factory is None:
                raise RuntimeError("no sandbox executor is configured")
            executor = self.executor_factory(task)
            if name == "git":
                command = ["git", *[str(value) for value in arguments.get("args", [])]]
            elif name.startswith("database_"):
                command = self.database_command(name, arguments)
            else:
                raw_command = arguments.get("command")
                if not isinstance(raw_command, list) or not raw_command:
                    raise ValueError("command must be a non-empty argv list")
                command = [str(value) for value in raw_command]
            cwd = "." if name == "database_migrate" else arguments.get("cwd")
            if cwd:
                self.workspace_path(str(cwd))
            requested = int(arguments.get("timeout", self.config.sandbox_timeout))
            timeout = min(max(requested, 1), self.config.sandbox_timeout)
            result = executor.run(command, cwd=str(cwd) if cwd else None, timeout=timeout)
            output = (f"exit_code={result.exit_code}\nstdout:\n{result.stdout}"
                      f"\nstderr:\n{result.stderr}")
            return self.redact(output)
        raise ValueError(f"unknown tool: {name}")
