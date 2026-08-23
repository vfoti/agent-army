"""Tests for the optional deepagents role runner (decision: selective adoption).

The pure-harness parts (sub-agent loading, the shared guarded toolbox, the
runner-selection flag, and the usage/envelope helpers) run everywhere. The
end-to-end pilot comparison additionally needs `pip install deepagents
langchain-anthropic` and is skipped otherwise, mirroring the sandbox
integration test in `tests/test_phase2.py`.
"""
from __future__ import annotations

import importlib.util
import itertools
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from harness import (  # noqa: E402
    BudgetGuard,
    Config,
    Task,
    TaskLedger,
    load_all_agents,
    load_subagents,
)
from harness.deepagents_runner import (  # noqa: E402
    DeepAgentsRoleRunner, _final_text, _usage_from_llm_result,
)
from harness.models import STATUS_BLOCKED, STATUS_SUCCEEDED  # noqa: E402
from harness.service import ROLE_RUNNERS, Service  # noqa: E402
from harness.intake import FolderIntake  # noqa: E402
from harness.anthropic_runner import AnthropicRoleRunner  # noqa: E402

AGENTS_DIR = REPO_ROOT / "agents"
HELLO_TASK = REPO_ROOT / "tasks" / "examples" / "hello-world-001.json"

DEEPAGENTS_AVAILABLE = all(
    importlib.util.find_spec(module) is not None
    for module in ("deepagents", "langchain_core")
)


def hello_task() -> Task:
    return Task.from_dict(json.loads(HELLO_TASK.read_text(encoding="utf-8")))


def _runner(cls, role: str, tmp: str, **overrides):
    config = Config()
    config.anthropic_api_key = "test-key"
    for key, value in overrides.items():
        setattr(config, key, value)
    guard = BudgetGuard(config, TaskLedger(Path(tmp) / "ledger"))
    agent = load_all_agents(AGENTS_DIR)[role]
    return cls(agent, AGENTS_DIR, config, guard, Path(tmp)), guard


class TestSubAgentLoading(unittest.TestCase):
    def test_loads_declared_subagents_for_every_role(self):
        agents = load_all_agents(AGENTS_DIR)
        for role, agent in agents.items():
            loaded = load_subagents(agent, AGENTS_DIR)
            self.assertEqual(len(loaded), len(agent.subagents), role)
            for definition in loaded:
                self.assertTrue(definition["name"])
                self.assertTrue(definition["description"])
                self.assertTrue(definition["prompt"])

    def test_names_are_delegation_slugs(self):
        agent = load_all_agents(AGENTS_DIR)["analysis"]
        names = [d["name"] for d in load_subagents(agent, AGENTS_DIR)]
        self.assertEqual(names, ["legacy-inventory", "behavior-extraction"])

    def test_rejects_subagent_path_outside_role_directory(self):
        agent = load_all_agents(AGENTS_DIR)["analysis"]
        agent.subagents = ["../../../etc/passwd"]
        with self.assertRaisesRegex(ValueError, "escapes"):
            load_subagents(agent, AGENTS_DIR)


class TestRunnerSelection(unittest.TestCase):
    def test_anthropic_is_the_default(self):
        self.assertEqual(Config().role_runner, "anthropic")

    def test_flag_selects_deepagents(self):
        with mock.patch.dict(
            os.environ, {"AGENT_ARMY_ROLE_RUNNER": "deepagents"}, clear=False,
        ):
            self.assertEqual(Config().role_runner, "deepagents")
        self.assertIs(ROLE_RUNNERS["anthropic"], AnthropicRoleRunner)
        self.assertIs(ROLE_RUNNERS["deepagents"], DeepAgentsRoleRunner)

    def test_service_rejects_unknown_runner(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Config()
            config.data_dir = Path(tmp)
            config.role_runner = "langgraph"
            intake = FolderIntake(Path(tmp) / "inbox", Path(tmp) / "outbox")
            with self.assertRaisesRegex(ValueError, "unknown role runner"):
                Service(config, intake)


class TestSharedGuards(unittest.TestCase):
    """Both runners must expose the same allow-listed, guarded toolbox."""

    def test_both_runners_allow_the_same_role_scoped_tools(self):
        with tempfile.TemporaryDirectory() as tmp:
            for role in ("analysis", "design", "code", "test"):
                anthropic, _ = _runner(AnthropicRoleRunner, role, tmp)
                deep, _ = _runner(DeepAgentsRoleRunner, role, tmp)
                self.assertEqual(
                    anthropic.toolbox.allowed_tools, deep.toolbox.allowed_tools, role)

    def test_undeclared_tool_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner, _ = _runner(DeepAgentsRoleRunner, "analysis", tmp)
            with self.assertRaisesRegex(ValueError, "not allowed"):
                runner.toolbox.execute(
                    hello_task(), "sandbox_exec", {"command": ["true"]})

    def test_workspace_confinement_and_call_cap(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner, _ = _runner(
                DeepAgentsRoleRunner, "analysis", tmp, sandbox_max_tool_calls=1)
            with self.assertRaisesRegex(ValueError, "escapes"):
                runner.toolbox.execute(
                    hello_task(), "doc_write",
                    {"path": "../escape.md", "content": "x"})
            runner.toolbox.reset_calls()
            runner.toolbox.count_call()
            with self.assertRaisesRegex(RuntimeError, "maximum tool-call count"):
                runner.toolbox.count_call()


class TestUsageAndEnvelopeHelpers(unittest.TestCase):
    def test_usage_from_message_metadata(self):
        generation = mock.Mock(message=mock.Mock(
            usage_metadata={"input_tokens": 12, "output_tokens": 3}))
        response = mock.Mock(generations=[[generation]])
        self.assertEqual(_usage_from_llm_result(response), (12, 3))

    def test_usage_falls_back_to_llm_output(self):
        response = mock.Mock(
            generations=[],
            llm_output={"token_usage": {"prompt_tokens": 7, "completion_tokens": 2}})
        self.assertEqual(_usage_from_llm_result(response), (7, 2))

    def test_usage_missing_is_zero(self):
        response = mock.Mock(generations=[], llm_output=None)
        self.assertEqual(_usage_from_llm_result(response), (0, 0))

    def test_final_text_uses_last_non_empty_message(self):
        state = {"messages": [
            mock.Mock(content="first"),
            mock.Mock(content=""),
        ]}
        self.assertEqual(_final_text(state), "first")

    def test_final_text_handles_content_blocks(self):
        state = {"messages": [mock.Mock(content=[{"type": "text", "text": "done"}])]}
        self.assertEqual(_final_text(state), "done")

    def test_final_text_of_empty_state(self):
        self.assertEqual(_final_text({}), "")


@unittest.skipUnless(
    DEEPAGENTS_AVAILABLE,
    "install deepagents to run the pilot comparison (pip install deepagents)",
)
class TestPilotThroughBothRunners(unittest.TestCase):
    """Step 6 of the adoption plan: the hello-world pilot must produce the
    same result-envelope contract, budget accounting, and blocking behaviour
    through either runner."""

    ENVELOPE = '{"status": "succeeded", "summary": "pilot ok"}'
    USAGE = {"input_tokens": 100, "output_tokens": 50, "total_tokens": 150}

    def _fake_chat_model(self, messages=None):
        from langchain_core.language_models.fake_chat_models import (
            GenericFakeChatModel,
        )
        from langchain_core.messages import AIMessage

        class _Fake(GenericFakeChatModel):
            def bind_tools(self, tools, **kwargs):
                return self

        stream = messages or itertools.cycle(
            [AIMessage(self.ENVELOPE, usage_metadata=self.USAGE)])
        return _Fake(messages=stream)

    def _deep_runner(self, tmp: str, messages=None, **overrides):
        runner, guard = _runner(DeepAgentsRoleRunner, "analysis", tmp, **overrides)
        runner._build_model = lambda: self._fake_chat_model(messages)
        return runner, guard

    def test_pilot_produces_the_same_envelope_as_the_anthropic_runner(self):
        task = hello_task()
        with tempfile.TemporaryDirectory() as tmp:
            deep, deep_guard = self._deep_runner(tmp)
            deep_result = deep.run(task, [])
            deep_usage = deep_guard.ledger.load(task.task_id)["usage"]
        with tempfile.TemporaryDirectory() as tmp:
            anthropic, anthropic_guard = _runner(
                AnthropicRoleRunner, "analysis", tmp)
            response = mock.Mock(
                content=[mock.Mock(type="text", text=self.ENVELOPE)],
                usage=mock.Mock(input_tokens=100, output_tokens=50))
            client = mock.Mock()
            client.messages.create.return_value = response
            module = mock.Mock()
            module.Anthropic.return_value = client
            with mock.patch.dict(sys.modules, {"anthropic": module}):
                anthropic_result = anthropic.run(task, [])
            anthropic_usage = anthropic_guard.ledger.load(task.task_id)["usage"]
        self.assertEqual(deep_result.status, STATUS_SUCCEEDED)
        self.assertEqual(deep_result.to_dict(), anthropic_result.to_dict())
        self.assertEqual(deep_usage["total_tokens"], 150)
        self.assertEqual(anthropic_usage["total_tokens"], 150)
        self.assertAlmostEqual(deep_usage["total_usd"], anthropic_usage["total_usd"])

    def test_budget_is_charged_per_model_call(self):
        from langchain_core.messages import AIMessage

        def stream():
            yield AIMessage("", usage_metadata=self.USAGE, tool_calls=[{
                "name": "doc_write",
                "args": {"path": "notes.md", "content": "x"},
                "id": "call-1",
            }])
            while True:
                yield AIMessage(self.ENVELOPE, usage_metadata=self.USAGE)

        task = hello_task()
        with tempfile.TemporaryDirectory() as tmp:
            runner, guard = self._deep_runner(
                tmp, messages=stream(), budget_tokens_per_task=100)
            result = runner.run(task, [])
            # The first call is recorded; the ceiling then blocks the second
            # rather than letting the role run to completion.
            self.assertEqual(result.status, STATUS_BLOCKED)
            self.assertIn("token ceiling reached", result.summary)
            self.assertEqual(
                guard.ledger.load(task.task_id)["usage"]["total_tokens"], 150)
            self.assertTrue((Path(tmp) / "notes.md").is_file())

    def test_tool_call_cap_blocks_the_role(self):
        from langchain_core.messages import AIMessage

        def stream():
            while True:
                yield AIMessage("", usage_metadata=self.USAGE, tool_calls=[{
                    "name": "doc_write",
                    "args": {"path": "notes.md", "content": "x"},
                    "id": "call-1",
                }])

        with tempfile.TemporaryDirectory() as tmp:
            runner, _ = self._deep_runner(
                tmp, messages=stream(), sandbox_max_tool_calls=0)
            result = runner.run(hello_task(), [])
        self.assertEqual(result.status, STATUS_BLOCKED)
        self.assertIn("maximum tool-call count", result.summary)

    def test_budget_blocks_before_any_model_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner, _ = self._deep_runner(tmp, budget_usd_per_task=0.0)
            result = runner.run(hello_task(), [])
        self.assertEqual(result.status, STATUS_BLOCKED)
        self.assertIn("cost ceiling reached", result.summary)

    def test_subagents_are_registered_with_role_scoped_tools_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner, _ = self._deep_runner(tmp)
            tools = runner._build_tools(hello_task())
            subagents = runner._build_subagents(tools)
            self.assertEqual(
                [s["name"] for s in subagents],
                ["legacy-inventory", "behavior-extraction"])
            for subagent in subagents:
                self.assertEqual(
                    [t.name for t in subagent["tools"]],
                    runner.toolbox.allowed_tools)

    def test_deepagents_filesystem_never_touches_the_host(self):
        """deepagents' built-in file tools use the in-memory state backend."""
        from langchain_core.messages import AIMessage

        def stream():
            yield AIMessage("", usage_metadata=self.USAGE, tool_calls=[{
                "name": "write_file",
                "args": {"file_path": "scratch.md", "content": "virtual"},
                "id": "call-1",
            }])
            while True:
                yield AIMessage(self.ENVELOPE, usage_metadata=self.USAGE)

        with tempfile.TemporaryDirectory() as tmp:
            runner, _ = self._deep_runner(tmp, messages=stream())
            result = runner.run(hello_task(), [])
            self.assertEqual(result.status, STATUS_SUCCEEDED)
            self.assertFalse((Path(tmp) / "scratch.md").exists())


if __name__ == "__main__":
    unittest.main()
