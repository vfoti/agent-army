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


@dataclass
class Config:
    # D1: Anthropic backend
    anthropic_api_key: str = field(
        default_factory=lambda: os.environ.get("ANTHROPIC_API_KEY", ""))
    anthropic_model: str = field(
        default_factory=lambda: os.environ.get("AGENT_ARMY_MODEL", "claude-sonnet-4-5"))
    max_output_tokens: int = field(
        default_factory=lambda: _env_int("AGENT_ARMY_MAX_OUTPUT_TOKENS", 4096))

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

    # D2: local Docker sandbox
    sandbox_image: str = field(
        default_factory=lambda: os.environ.get("AGENT_ARMY_SANDBOX_IMAGE", "python:3.12-slim"))
    sandbox_timeout: int = field(
        default_factory=lambda: _env_int("AGENT_ARMY_SANDBOX_TIMEOUT", 600))

    # D4/D6: always-on loop + file ledger
    poll_interval_seconds: int = field(
        default_factory=lambda: _env_int("AGENT_ARMY_POLL_INTERVAL", 60))
    data_dir: Path = field(
        default_factory=lambda: Path(os.environ.get("AGENT_ARMY_DATA_DIR", "tasks")))
