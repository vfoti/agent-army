"""Anthropic-backed role runner (decision D1).

Calls the Anthropic Messages API with the composed role prompt, requires the
model to answer with a JSON result envelope, retries once on malformed
output, and enforces budget guards (D5) before and after every call.

The `anthropic` SDK is imported lazily so the rest of the harness stays
dependency-free; install with `pip install anthropic`.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

from .budget import BudgetExceeded, BudgetGuard
from .config import Config
from .models import AgentDefinition, Result, Task, STATUS_BLOCKED
from .runners import PromptRoleRunner, RoleRunner

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
    ) -> None:
        super().__init__(agent, agents_dir)
        self.config = config
        self.budget = budget
        self._prompter = PromptRoleRunner(agent, agents_dir, invoke=lambda s, u: {})

    def _call_model(self, task_id: str, system_prompt: str, messages: List[Dict[str, str]]) -> str:
        import anthropic  # lazy import (see module docstring)

        self.budget.check(task_id, self.agent.role)
        client = anthropic.Anthropic(api_key=self.config.anthropic_api_key)
        response = client.messages.create(
            model=self.config.anthropic_model,
            max_tokens=self.config.max_output_tokens,
            system=system_prompt,
            messages=messages,
        )
        self.budget.record(
            task_id,
            self.agent.role,
            response.usage.input_tokens,
            response.usage.output_tokens,
        )
        return "".join(block.text for block in response.content if block.type == "text")

    def run(self, task: Task, prior_results: List[Dict[str, Any]]) -> Result:
        from .agent_loader import build_system_prompt

        system_prompt = (
            build_system_prompt(self.agent, self.agents_dir)
            + "\n\n" + ENVELOPE_INSTRUCTIONS
        )
        user_prompt = self._prompter.build_user_prompt(task, prior_results)
        messages: List[Dict[str, str]] = [{"role": "user", "content": user_prompt}]
        try:
            text = self._call_model(task.task_id, system_prompt, messages)
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
                text = self._call_model(task.task_id, system_prompt, messages)
                raw = _extract_json(text)
        except BudgetExceeded as exc:
            return Result(
                task_id=task.task_id,
                role=self.agent.role,
                status=STATUS_BLOCKED,
                summary=f"Budget guard stopped this role: {exc}",
            )
        raw.setdefault("task_id", task.task_id)
        raw.setdefault("role", self.agent.role)
        return Result.from_dict(raw)
