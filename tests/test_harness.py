"""Tests for the agent army harness (stdlib unittest, no dependencies)."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from harness import (  # noqa: E402
    FolderIntake,
    LocalExecutor,
    NullRoleRunner,
    Orchestrator,
    PromptRoleRunner,
    Result,
    Task,
    TaskLedger,
    build_system_prompt,
    load_all_agents,
)
from harness.models import STATUS_AWAITING_APPROVAL, STATUS_SUCCEEDED  # noqa: E402

AGENTS_DIR = REPO_ROOT / "agents"
EXAMPLE_TASK = REPO_ROOT / "tasks" / "examples" / "modernize-billing-001.json"


def example_task() -> Task:
    return Task.from_dict(json.loads(EXAMPLE_TASK.read_text(encoding="utf-8")))


class TestAgentLoader(unittest.TestCase):
    def test_loads_all_four_roles(self):
        agents = load_all_agents(AGENTS_DIR)
        self.assertEqual(set(agents), {"analysis", "design", "code", "test"})

    def test_frontmatter_fields(self):
        agents = load_all_agents(AGENTS_DIR)
        analysis = agents["analysis"]
        self.assertEqual(analysis.name, "analysis-agent")
        self.assertIn("repo_read", analysis.tools)
        self.assertEqual(analysis.handoff["next_role"], "design")
        self.assertEqual(len(analysis.subagents), 2)
        self.assertIsNone(agents["test"].handoff["next_role"])
        self.assertIn("database_query", analysis.tools)
        self.assertNotIn("database_migrate", analysis.tools)
        self.assertIn("database_migrate", agents["code"].tools)
        self.assertIn("database_migrate", agents["test"].tools)

    def test_system_prompt_includes_shared_instructions(self):
        agents = load_all_agents(AGENTS_DIR)
        prompt = build_system_prompt(agents["design"], AGENTS_DIR)
        self.assertIn("Shared Performance Instructions", prompt)
        self.assertIn("Agent: Design", prompt)


class TestTaskModel(unittest.TestCase):
    def test_example_task_parses(self):
        task = example_task()
        self.assertEqual(task.task_id, "modernize-billing-001")
        self.assertEqual(task.roles, ["analysis", "design", "code", "test"])

    def test_rejects_unknown_role(self):
        with self.assertRaises(ValueError):
            Task(task_id="t", source={"system": "x"}, goal="g", roles=["deploy"])

    def test_requires_goal(self):
        with self.assertRaises(ValueError):
            Task(task_id="t", source={"system": "x"}, goal="", roles=["analysis"])


class TestFolderIntake(unittest.TestCase):
    def test_poll_and_deliver(self):
        with tempfile.TemporaryDirectory() as tmp:
            inbox = Path(tmp) / "inbox"
            outbox = Path(tmp) / "outbox"
            inbox.mkdir()
            (inbox / "t1.json").write_text(EXAMPLE_TASK.read_text(encoding="utf-8"))
            intake = FolderIntake(inbox, outbox)
            tasks = intake.poll()
            self.assertEqual(len(tasks), 1)
            intake.deliver(Result(task_id="t1", role="analysis", status=STATUS_SUCCEEDED))
            self.assertTrue((outbox / "t1.analysis.result.json").exists())


class TestOrchestrator(unittest.TestCase):
    def _orchestrator(self, tmp: str) -> Orchestrator:
        agents = load_all_agents(AGENTS_DIR)
        runners = {r: NullRoleRunner(a, AGENTS_DIR) for r, a in agents.items()}
        return Orchestrator(runners, TaskLedger(Path(tmp) / "ledger"))

    def test_pauses_at_analysis_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            orch = self._orchestrator(tmp)
            task = example_task()
            results = orch.advance(task)
            self.assertEqual(results[0].role, "analysis")
            self.assertEqual(results[0].status, STATUS_SUCCEEDED)
            self.assertEqual(results[-1].status, STATUS_AWAITING_APPROVAL)

    def test_resumes_after_approvals(self):
        with tempfile.TemporaryDirectory() as tmp:
            orch = self._orchestrator(tmp)
            task = example_task()
            orch.advance(task)
            orch.approve(task.task_id, "analysis", "reviewer")
            results = orch.advance(task)
            self.assertEqual(results[0].role, "design")
            orch.approve(task.task_id, "design", "reviewer")
            results = orch.advance(task)
            # code has no gate before test, so both run
            self.assertEqual([r.role for r in results], ["code", "test"])
            completed = {r["role"] for r in orch.ledger.results_for(task.task_id)
                         if r["status"] == STATUS_SUCCEEDED}
            self.assertEqual(completed, {"analysis", "design", "code", "test"})

    def test_advance_is_idempotent_when_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            orch = self._orchestrator(tmp)
            task = example_task()
            orch.advance(task)
            results = orch.advance(task)
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0].status, STATUS_AWAITING_APPROVAL)


class TestPromptRoleRunner(unittest.TestCase):
    def test_invokes_with_composed_prompts(self):
        agents = load_all_agents(AGENTS_DIR)
        captured = {}

        def invoke(system_prompt: str, user_prompt: str):
            captured["system"] = system_prompt
            captured["user"] = user_prompt
            return {"status": STATUS_SUCCEEDED, "summary": "ok"}

        runner = PromptRoleRunner(agents["analysis"], AGENTS_DIR, invoke)
        prior = [{"role": "analysis", "summary": "prev", "artifacts": []}]
        result = runner.run(example_task(), prior)
        self.assertEqual(result.role, "analysis")
        self.assertIn("Shared Performance Instructions", captured["system"])
        self.assertIn("Goal: Modernize the billing domain", captured["user"])
        self.assertIn("[analysis] prev", captured["user"])


class TestLocalExecutor(unittest.TestCase):
    def test_runs_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            executor = LocalExecutor(Path(tmp))
            result = executor.run(["python3", "-c", "print('hello')"])
            self.assertTrue(result.ok)
            self.assertIn("hello", result.stdout)


if __name__ == "__main__":
    unittest.main()
