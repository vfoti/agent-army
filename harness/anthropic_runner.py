"""Anthropic-backed role runner (decision D1).

Calls the Anthropic Messages API with the composed role prompt, requires the
model to answer with a JSON result envelope, retries once on malformed
output, and enforces budget guards (D5) before and after every call.

The `anthropic` SDK is imported lazily so the rest of the harness stays
dependency-free; install with `pip install anthropic`.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .budget import BudgetExceeded, BudgetGuard
from .config import Config
from .models import AgentDefinition, Result, Task, STATUS_BLOCKED
from .runners import PromptRoleRunner, RoleRunner
from .sandbox import SandboxExecutor

ENVELOPE_INSTRUCTIONS = """
Respond ONLY with a JSON object matching this result envelope schema:
{
  "status": "succeeded" | "failed" | "blocked",
  "summary": "<one-paragraph summary of what you did>",
  "artifacts": [{"type": "file"|"pull_request"|"evidence"|"report", "reference": "<path or url>", "description": "<short>"}],
  "open_questions": ["<question>", ...]
}
Do not wrap the JSON in markdown fences or add any other text.
""".strip()


def _extract_json(text: str) -> Dict[str, Any]:
    """Parse a JSON object from model output, tolerating markdown fences."""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("no JSON object found in model output")
    return json.loads(text[start:end + 1])


class AnthropicRoleRunner(RoleRunner):
    def __init__(
        self,
        agent: AgentDefinition,
        agents_dir: Path,
        config: Config,
        budget: BudgetGuard,
        workspace: Optional[Path] = None,
        executor_factory: Optional[Callable[[Task], SandboxExecutor]] = None,
    ) -> None:
        super().__init__(agent, agents_dir)
        self.config = config
        self.budget = budget
        self.workspace = (workspace or Path.cwd()).resolve()
        self.executor_factory = executor_factory
        self._prompter = PromptRoleRunner(agent, agents_dir, invoke=lambda s, u: {})

    def _tool_definitions(self) -> List[Dict[str, Any]]:
        definitions = {
            "repo_read": {
                "name": "repo_read", "description": "Read a UTF-8 file in the task workspace.",
                "input_schema": {"type": "object", "properties": {
                    "path": {"type": "string"}}, "required": ["path"]},
            },
            "repo_write": {
                "name": "repo_write", "description": "Write a UTF-8 file in the task workspace.",
                "input_schema": {"type": "object", "properties": {
                    "path": {"type": "string"}, "content": {"type": "string"}},
                    "required": ["path", "content"]},
            },
            "doc_write": {
                "name": "doc_write", "description": "Write a UTF-8 document in the task workspace.",
                "input_schema": {"type": "object", "properties": {
                    "path": {"type": "string"}, "content": {"type": "string"}},
                    "required": ["path", "content"]},
            },
            "sandbox_exec": {
                "name": "sandbox_exec", "description": "Run an argv command in the configured sandbox.",
                "input_schema": {"type": "object", "properties": {
                    "command": {"type": "array", "items": {"type": "string"}, "minItems": 1},
                    "cwd": {"type": "string"},
                    "timeout": {"type": "integer", "minimum": 1}},
                    "required": ["command"]},
            },
            "git": {
                "name": "git", "description": "Run a git command in the configured sandbox.",
                "input_schema": {"type": "object", "properties": {
                    "args": {"type": "array", "items": {"type": "string"}},
                    "cwd": {"type": "string"}}, "required": ["args"]},
            },
            "database_query": {
                "name": "database_query",
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
                "name": "database_schema",
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
                "name": "database_migrate",
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
        return [definitions[name] for name in self.agent.tools if name in definitions]

    def _workspace_path(self, raw: str) -> Path:
        path = (self.workspace / raw).resolve()
        try:
            path.relative_to(self.workspace)
        except ValueError as exc:
            raise ValueError("path escapes task workspace") from exc
        return path

    def _redact(self, text: str) -> str:
        for secret in (self.config.anthropic_api_key, self.config.github_token):
            if secret:
                text = text.replace(secret, "[REDACTED]")
        return text[:self.config.sandbox_max_output_chars]

    @staticmethod
    def _read_only_sql(sql: str) -> str:
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

    def _database_command(self, name: str, arguments: Dict[str, Any]) -> List[str]:
        if name == "database_migrate":
            if not self.config.postgres_service:
                raise RuntimeError("AGENT_ARMY_POSTGRES_SERVICE is not configured")
            raw_path = str(arguments["path"])
            path = self._workspace_path(raw_path)
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
            sql = self._read_only_sql(str(arguments["sql"]))
            return [
                "psql", connection, "--no-psqlrc", "--set=ON_ERROR_STOP=1",
                "--csv", "--command", f"BEGIN READ ONLY; {sql}; COMMIT;",
            ]

        if not self.config.db2_database:
            raise RuntimeError("AGENT_ARMY_DB2_DATABASE is not configured")
        if name == "database_schema":
            return ["db2look", "-d", self.config.db2_database, "-e", "-x"]
        sql = self._read_only_sql(str(arguments["sql"]))
        return [
            "db2", "-x", "-td;",
            f"connect to {self.config.db2_database}; {sql}; connect reset;",
        ]

    def _execute_tool(
        self, task: Task, name: str, arguments: Dict[str, Any],
    ) -> str:
        if name not in self.agent.tools:
            raise ValueError(f"tool {name!r} is not allowed for role {self.agent.role}")
        if name == "repo_read":
            return self._redact(self._workspace_path(str(arguments["path"])).read_text(
                encoding="utf-8"))
        if name in {"repo_write", "doc_write"}:
            path = self._workspace_path(str(arguments["path"]))
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(str(arguments["content"]), encoding="utf-8")
            return f"wrote {path.relative_to(self.workspace)}"
        if name in {
            "sandbox_exec", "git", "database_query", "database_schema",
            "database_migrate",
        }:
            if self.executor_factory is None:
                raise RuntimeError("no sandbox executor is configured")
            executor = self.executor_factory(task)
            if name == "git":
                command = ["git", *[str(value) for value in arguments.get("args", [])]]
            elif name.startswith("database_"):
                command = self._database_command(name, arguments)
            else:
                raw_command = arguments.get("command")
                if not isinstance(raw_command, list) or not raw_command:
                    raise ValueError("command must be a non-empty argv list")
                command = [str(value) for value in raw_command]
            cwd = "." if name == "database_migrate" else arguments.get("cwd")
            if cwd:
                self._workspace_path(str(cwd))
            requested = int(arguments.get("timeout", self.config.sandbox_timeout))
            timeout = min(max(requested, 1), self.config.sandbox_timeout)
            result = executor.run(command, cwd=str(cwd) if cwd else None, timeout=timeout)
            output = (f"exit_code={result.exit_code}\nstdout:\n{result.stdout}"
                      f"\nstderr:\n{result.stderr}")
            return self._redact(output)
        raise ValueError(f"unknown tool: {name}")

    def _call_model(
        self, task_id: str, system_prompt: str, messages: List[Dict[str, Any]],
    ) -> Any:
        import anthropic  # lazy import (see module docstring)

        self.budget.check(task_id, self.agent.role)
        client = anthropic.Anthropic(api_key=self.config.anthropic_api_key)
        kwargs: Dict[str, Any] = {
            "model": self.config.anthropic_model,
            "max_tokens": self.config.max_output_tokens,
            "system": system_prompt,
            "messages": messages,
        }
        tools = self._tool_definitions()
        if tools:
            kwargs["tools"] = tools
        response = client.messages.create(
            **kwargs,
        )
        self.budget.record(
            task_id,
            self.agent.role,
            response.usage.input_tokens,
            response.usage.output_tokens,
        )
        return response

    @staticmethod
    def _response_text(response: Any) -> str:
        return "".join(block.text for block in response.content if block.type == "text")

    def run(self, task: Task, prior_results: List[Dict[str, Any]]) -> Result:
        from .agent_loader import build_system_prompt

        system_prompt = (
            build_system_prompt(self.agent, self.agents_dir)
            + "\n\n" + ENVELOPE_INSTRUCTIONS
        )
        user_prompt = self._prompter.build_user_prompt(task, prior_results)
        messages: List[Dict[str, Any]] = [{"role": "user", "content": user_prompt}]
        try:
            tool_calls = 0
            while True:
                response = self._call_model(task.task_id, system_prompt, messages)
                uses = [block for block in response.content if block.type == "tool_use"]
                if not uses:
                    break
                messages.append({"role": "assistant", "content": response.content})
                results = []
                for use in uses:
                    tool_calls += 1
                    if tool_calls > self.config.sandbox_max_tool_calls:
                        raise RuntimeError("maximum tool-call count exceeded")
                    try:
                        content = self._execute_tool(task, use.name, use.input)
                        is_error = False
                    except RuntimeError:
                        raise
                    except Exception as exc:  # tool errors are returned to the model
                        content = self._redact(f"{type(exc).__name__}: {exc}")
                        is_error = True
                    results.append({
                        "type": "tool_result", "tool_use_id": use.id,
                        "content": content, "is_error": is_error,
                    })
                messages.append({"role": "user", "content": results})
            text = self._response_text(response)
            try:
                raw = _extract_json(text)
            except (ValueError, json.JSONDecodeError):
                # One retry: ask the model to reformat its answer as JSON.
                messages.append({"role": "assistant", "content": text})
                messages.append({
                    "role": "user",
                    "content": "Your previous reply was not a valid JSON result "
                               "envelope. Reply again with ONLY the JSON object.",
                })
                response = self._call_model(task.task_id, system_prompt, messages)
                text = self._response_text(response)
                raw = _extract_json(text)
        except (BudgetExceeded, RuntimeError) as exc:
            return Result(
                task_id=task.task_id,
                role=self.agent.role,
                status=STATUS_BLOCKED,
                summary=f"Role execution was blocked: {exc}",
            )
        raw.setdefault("task_id", task.task_id)
        raw.setdefault("role", self.agent.role)
        return Result.from_dict(raw)
