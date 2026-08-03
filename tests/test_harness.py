import tempfile
import unittest
from pathlib import Path

from harness.runtime import (
    AgentHarness, ApprovalRequired, HarnessError, RunRequest, ToolPolicy, WorkspaceTools,
)


class FakeModel:
    def complete(self, messages, tools):
        return {"content": "done", "tool_calls": []}


class HarnessTests(unittest.TestCase):
    def test_registry_loads_repository_agents(self):
        harness = AgentHarness(Path(__file__).parents[1], FakeModel())
        self.assertIn("requirements-discovery", harness.registry.agents)
        self.assertIn("Keep context windows small", harness.registry.shared_instructions)
        self.assertIn("legacy-inventory", harness.registry.subagents)

    def test_workspace_cannot_escape(self):
        with tempfile.TemporaryDirectory() as directory:
            policy = ToolPolicy(Path(directory).resolve())
            with self.assertRaises(HarnessError):
                policy.path("../outside")

    def test_existing_file_requires_approval(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "file.txt"
            target.write_text("old", encoding="utf-8")
            with self.assertRaises(ApprovalRequired):
                WorkspaceTools(ToolPolicy(root)).write("file.txt", "new")

    def test_run_returns_completed_result(self):
        harness = AgentHarness(Path(__file__).parents[1], FakeModel())
        result = harness.run(RunRequest("inventory the repository"))
        self.assertEqual(result.status, "completed")


if __name__ == "__main__":
    unittest.main()
