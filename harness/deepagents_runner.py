"""deepagents-backed role runner (optional, opt-in).

Runs a role with LangChain's Python `deepagents` harness instead of the
hand-rolled tool loop in `anthropic_runner.py`. What deepagents contributes is
the *inner* agent loop: todo-based planning, context management, and — the
main reason to adopt it — executable delegation to the narrow sub-agents that
`agents/<role>/subagents/*.subagent.md` currently only describe in prose.

What deepagents explicitly does **not** own here: intake, governance gates,
the ledger, the Task/Result envelope contract, and the security guards.
deepagents follows a "trust the LLM" model, so this runner deliberately does
not inherit its defaults:

- Its filesystem tools are backed by the in-memory `StateBackend`, which never
  touches the host. It is scratch space for the agent's own context
  management; every durable read/write goes through the guarded harness tools.
- The only tools reaching the workspace or a shell are the allow-listed
  `RoleToolbox` tools from the role's agent-definition frontmatter, which keep
  workspace confinement, argv-only execution, timeout/output caps, token
  redaction, and read-only SQL enforcement.
- `BudgetGuard` is enforced *per model call* through a callback handler rather
  than once per role, because a deep agent makes many calls per role.

`deepagents` and its LangChain dependencies are imported lazily, so the
harness stays stdlib-only for anyone who does not opt in. Install with
`pip install deepagents langchain-anthropic` and set
`AGENT_ARMY_ROLE_RUNNER=deepagents`.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .agent_loader import build_system_prompt, load_subagents
from .anthropic_runner import ENVELOPE_INSTRUCTIONS, _extract_json
from .budget import BudgetExceeded, BudgetGuard
from .config import Config
from .models import AgentDefinition, Result, Task, STATUS_BLOCKED
from .runners import PromptRoleRunner, RoleRunner
from .sandbox import SandboxExecutor
from .tools import TOOL_SCHEMAS, RoleToolbox

DEEPAGENTS_INSTRUCTIONS = """
You are running inside the deepagents harness.

Use the planning tool to break the task down, and delegate narrow work to your
sub-agents so each keeps a small context window.

Your built-in filesystem is virtual scratch space and is discarded when the
role finishes. Anything that must survive — documents, code, migrations —
must be written to the task workspace with the harness tools available to you
(for example `doc_write` or `repo_write`), and referenced by workspace-relative
path in your final result envelope.
""".strip()


def _usage_from_llm_result(response: Any) -> tuple:
    """Best-effort (input_tokens, output_tokens) from a LangChain LLMResult."""
    for generations in getattr(response, "generations", []) or []:
        for generation in generations:
            message = getattr(generation, "message", None)
            usage = getattr(message, "usage_metadata", None)
            if usage:
                return int(usage.get("input_tokens", 0)), int(usage.get("output_tokens", 0))
    output = getattr(response, "llm_output", None) or {}
    usage = output.get("token_usage") or output.get("usage") or {}
    return (
        int(usage.get("prompt_tokens", usage.get("input_tokens", 0)) or 0),
        int(usage.get("completion_tokens", usage.get("output_tokens", 0)) or 0),
    )


def _budget_callback_handler(budget: BudgetGuard, task_id: str, role: str) -> Any:
    """A LangChain callback that enforces the ceilings on every model call.

    Defined inside a function so `langchain_core` is only imported when the
    deepagents runner is actually used.
    """
    from langchain_core.callbacks import BaseCallbackHandler

    class _BudgetCallbackHandler(BaseCallbackHandler):
        raise_error = True

        def _check(self) -> None:
            budget.check(task_id, role)

        def on_llm_start(self, serialized, prompts, **kwargs) -> None:
            self._check()

        def on_chat_model_start(self, serialized, messages, **kwargs) -> None:
            self._check()

        def on_llm_end(self, response, **kwargs) -> None:
            input_tokens, output_tokens = _usage_from_llm_result(response)
            if input_tokens or output_tokens:
                budget.record(task_id, role, input_tokens, output_tokens)

    return _BudgetCallbackHandler()


def _final_text(state: Any) -> str:
    """Text of the last AI message in a deepagents invocation result."""
    messages = (state or {}).get("messages", []) if isinstance(state, dict) else []
    for message in reversed(messages):
        content = getattr(message, "content", None)
        if content is None and isinstance(message, dict):
            content = message.get("content")
        if isinstance(content, list):
            content = "".join(
                part.get("text", "") for part in content if isinstance(part, dict))
        if isinstance(content, str) and content.strip():
            return content
    return ""


class DeepAgentsRoleRunner(RoleRunner):
    """Runs one role through `deepagents.create_deep_agent`.

    Takes the same constructor arguments as `AnthropicRoleRunner` so the two
    are interchangeable behind the `AGENT_ARMY_ROLE_RUNNER` config flag.
    """

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
        self.toolbox = RoleToolbox(agent, config, workspace, executor_factory)
        self._prompter = PromptRoleRunner(agent, agents_dir, invoke=lambda s, u: {})

    def _build_tools(self, task: Task) -> List[Any]:
        """Wrap each allow-listed harness tool as a LangChain structured tool.

        Every call is charged against the tool-call cap and executed by
        `RoleToolbox`, so deepagents never gets an unguarded path to the
        workspace, the shell, or the databases.
        """
        from langchain_core.tools import StructuredTool

        def make(name: str) -> Any:
            schema = dict(TOOL_SCHEMAS[name]["input_schema"])

            def call(**arguments: Any) -> str:
                self.toolbox.count_call()
                return self.toolbox.execute(task, name, arguments)

            return StructuredTool.from_function(
                func=call,
                name=name,
                description=TOOL_SCHEMAS[name]["description"],
                args_schema=schema,
            )

        return [make(name) for name in self.toolbox.allowed_tools]

    def _build_subagents(self, tools: List[Any]) -> List[Dict[str, Any]]:
        """Map the role's `*.subagent.md` files onto deepagents sub-agents.

        Sub-agents inherit the role's allow-listed tools only — delegation
        never widens what the role itself is permitted to do.
        """
        subagents: List[Dict[str, Any]] = []
        for definition in load_subagents(self.agent, self.agents_dir):
            subagents.append({
                "name": definition["name"],
                "description": definition["description"],
                "system_prompt": definition["prompt"],
                "tools": tools,
            })
        return subagents

    def _build_agent(self, task: Task) -> Any:
        from deepagents import create_deep_agent
        from deepagents.backends import StateBackend

        system_prompt = "\n\n".join([
            build_system_prompt(self.agent, self.agents_dir),
            DEEPAGENTS_INSTRUCTIONS,
            ENVELOPE_INSTRUCTIONS,
        ])
        tools = self._build_tools(task)
        return create_deep_agent(
            model=self._build_model(),
            tools=tools,
            system_prompt=system_prompt,
            subagents=self._build_subagents(tools),
            # In-memory filesystem: deepagents' own file tools never reach the
            # host. Durable I/O goes through the guarded tools above.
            backend=StateBackend(),
        )

    def _build_model(self) -> Any:
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(
            model=self.config.anthropic_model,
            api_key=self.config.anthropic_api_key,
            max_tokens=self.config.max_output_tokens,
        )

    def run(self, task: Task, prior_results: List[Dict[str, Any]]) -> Result:
        user_prompt = self._prompter.build_user_prompt(task, prior_results)
        try:
            self.toolbox.reset_calls()
            self.budget.check(task.task_id, self.agent.role)
            agent = self._build_agent(task)
            state = agent.invoke(
                {"messages": [{"role": "user", "content": user_prompt}]},
                config={
                    "callbacks": [
                        _budget_callback_handler(
                            self.budget, task.task_id, self.agent.role),
                    ],
                    "recursion_limit": self.config.deepagents_recursion_limit,
                },
            )
            raw = _extract_json(_final_text(state))
        except (BudgetExceeded, RuntimeError, ValueError, json.JSONDecodeError) as exc:
            return Result(
                task_id=task.task_id,
                role=self.agent.role,
                status=STATUS_BLOCKED,
                summary=self.toolbox.redact(
                    f"Role execution was blocked: {type(exc).__name__}: {exc}"),
            )
        raw.setdefault("task_id", task.task_id)
        raw.setdefault("role", self.agent.role)
        return Result.from_dict(raw)
