"""Environment-variable configuration for the harness (decision D5).

All secrets and tunables come from env vars so the always-on local
deployment (D4) can be configured via a `.env` file / container environment.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    return float(raw) if raw else default


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    return int(raw) if raw else default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    return raw.lower() in {"1", "true", "yes", "on"} if raw else default


@dataclass
class Config:
    # D1: Anthropic backend
    anthropic_api_key: str = field(
        default_factory=lambda: os.environ.get("ANTHROPIC_API_KEY", ""))
    anthropic_model: str = field(
        default_factory=lambda: os.environ.get("AGENT_ARMY_MODEL", "claude-sonnet-4-5"))
    max_output_tokens: int = field(
        default_factory=lambda: _env_int("AGENT_ARMY_MAX_OUTPUT_TOKENS", 4096))

    # Role execution backend. "anthropic" is the default hand-rolled tool loop;
    # "deepagents" opts in to LangChain's Python deepagents harness, which adds
    # planning and executable sub-agent delegation but pulls in the LangChain
    # dependency tree. See docs/harness.md.
    role_runner: str = field(
        default_factory=lambda: os.environ.get("AGENT_ARMY_ROLE_RUNNER", "anthropic"))
    # Upper bound on deepagents' internal graph steps for a single role, so a
    # looping agent fails fast instead of burning the whole task budget.
    deepagents_recursion_limit: int = field(
        default_factory=lambda: _env_int("AGENT_ARMY_DEEPAGENTS_RECURSION_LIMIT", 50))

    # D3: GitHub issue intake
    github_token: str = field(
        default_factory=lambda: os.environ.get("GITHUB_TOKEN", ""))
    github_repo: str = field(
        default_factory=lambda: os.environ.get("AGENT_ARMY_REPO", ""))
    intake_label: str = field(
        default_factory=lambda: os.environ.get("AGENT_ARMY_INTAKE_LABEL", "agent-task"))

    # D5: budget guards (hard ceilings; the runner refuses to start/continue past them)
    budget_usd_per_task: float = field(
        default_factory=lambda: _env_float("AGENT_ARMY_BUDGET_USD_PER_TASK", 5.0))
    budget_usd_per_role: float = field(
        default_factory=lambda: _env_float("AGENT_ARMY_BUDGET_USD_PER_ROLE", 2.0))
    budget_tokens_per_task: int = field(
        default_factory=lambda: _env_int("AGENT_ARMY_BUDGET_TOKENS_PER_TASK", 500_000))
    # Approximate pricing (USD per million tokens); override to match your model.
    price_per_mtok_input: float = field(
        default_factory=lambda: _env_float("AGENT_ARMY_PRICE_INPUT_PER_MTOK", 3.0))
    price_per_mtok_output: float = field(
        default_factory=lambda: _env_float("AGENT_ARMY_PRICE_OUTPUT_PER_MTOK", 15.0))

    # Sandboxed command execution. Docker Sandboxes is opt-in.
    sandbox_backend: str = field(
        default_factory=lambda: os.environ.get("AGENT_ARMY_SANDBOX_BACKEND", "local"))
    sandbox_image: str = field(
        default_factory=lambda: os.environ.get("AGENT_ARMY_SANDBOX_IMAGE", "python:3.12-slim"))
    sandbox_timeout: int = field(
        default_factory=lambda: _env_int("AGENT_ARMY_SANDBOX_TIMEOUT", 600))
    sandbox_template: str = field(
        default_factory=lambda: os.environ.get("AGENT_ARMY_SANDBOX_TEMPLATE", "shell"))
    sandbox_clone: bool = field(
        default_factory=lambda: _env_bool("AGENT_ARMY_SANDBOX_CLONE", False))
    sandbox_retain: bool = field(
        default_factory=lambda: _env_bool("AGENT_ARMY_SANDBOX_RETAIN", False))
    sandbox_max_tool_calls: int = field(
        default_factory=lambda: _env_int("AGENT_ARMY_SANDBOX_MAX_TOOL_CALLS", 20))
    sandbox_max_output_chars: int = field(
        default_factory=lambda: _env_int("AGENT_ARMY_SANDBOX_MAX_OUTPUT_CHARS", 50_000))

    # Database tools. Authentication stays in CLI-native profiles so secrets
    # are never accepted as model tool arguments.
    db2_database: str = field(
        default_factory=lambda: os.environ.get("AGENT_ARMY_DB2_DATABASE", ""))
    postgres_service: str = field(
        default_factory=lambda: os.environ.get("AGENT_ARMY_POSTGRES_SERVICE", ""))

    # D4/D6: always-on loop + file ledger
    poll_interval_seconds: int = field(
        default_factory=lambda: _env_int("AGENT_ARMY_POLL_INTERVAL", 60))
    data_dir: Path = field(
        default_factory=lambda: Path(os.environ.get("AGENT_ARMY_DATA_DIR", "tasks")))
