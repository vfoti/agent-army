"""Tests for Phase 2 components: budget guards, Anthropic runner (mocked),
GitHub issue intake (mocked HTTP), Docker executor command construction,
and the always-on service loop."""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from harness import (  # noqa: E402
    BudgetExceeded,
    BudgetGuard,
    Config,
    GitHubIssueIntake,
    Result,
    Task,
    TaskLedger,
    load_all_agents,
)
from harness.anthropic_runner import AnthropicRoleRunner, _extract_json  # noqa: E402
from harness.models import STATUS_BLOCKED, STATUS_SUCCEEDED  # noqa: E402
from harness.sandbox import DockerExecutor, DockerSandboxExecutor  # noqa: E402
from harness.service import Service  # noqa: E402
from harness.intake import FolderIntake  # noqa: E402

AGENTS_DIR = REPO_ROOT / "agents"
HELLO_TASK = REPO_ROOT / "tasks" / "examples" / "hello-world-001.json"


def hello_task() -> Task:
    return Task.from_dict(json.loads(HELLO_TASK.read_text(encoding="utf-8")))


class TestBudgetGuard(unittest.TestCase):
    def _guard(self, tmp: str, **overrides) -> BudgetGuard:
        config = Config()
        for k, v in overrides.items():
            setattr(config, k, v)
        return BudgetGuard(config, TaskLedger(Path(tmp)))

    def test_records_and_accumulates_usage(self):
        with tempfile.TemporaryDirectory() as tmp:
            guard = self._guard(tmp)
            guard.record("t1", "analysis", 1000, 500)
            guard.record("t1", "design", 2000, 100)
            usage = guard.ledger.load("t1")["usage"]
            self.assertEqual(usage["total_tokens"], 3600)
            self.assertGreater(usage["total_usd"], 0)

    def test_task_cost_ceiling(self):
        with tempfile.TemporaryDirectory() as tmp:
            guard = self._guard(tmp, budget_usd_per_task=0.001)
            guard.record("t1", "analysis", 100_000, 10_000)
            with self.assertRaises(BudgetExceeded):
                guard.check("t1", "analysis")

    def test_role_cost_ceiling(self):
        with tempfile.TemporaryDirectory() as tmp:
            guard = self._guard(tmp, budget_usd_per_role=0.001,
                                budget_usd_per_task=1000.0)
            guard.record("t1", "analysis", 100_000, 10_000)
            with self.assertRaises(BudgetExceeded):
                guard.check("t1", "analysis")
            guard.check("t1", "design")  # other role still allowed

    def test_token_ceiling(self):
        with tempfile.TemporaryDirectory() as tmp:
            guard = self._guard(tmp, budget_tokens_per_task=100)
            guard.record("t1", "analysis", 90, 20)
            with self.assertRaises(BudgetExceeded):
                guard.check("t1", "analysis")


class TestExtractJson(unittest.TestCase):
    def test_plain_json(self):
        self.assertEqual(_extract_json('{"status": "succeeded"}'),
                         {"status": "succeeded"})

    def test_fenced_json(self):
        text = '```json\n{"status": "succeeded"}\n```'
        self.assertEqual(_extract_json(text), {"status": "succeeded"})

    def test_json_with_prose(self):
        text = 'Here is my result:\n{"status": "failed"}\nThanks!'
        self.assertEqual(_extract_json(text), {"status": "failed"})

    def test_no_json_raises(self):
        with self.assertRaises(ValueError):
            _extract_json("no json here")


class _FakeResponse:
    def __init__(self, text: str, input_tokens=100, output_tokens=50):
        block = mock.Mock()
        block.type = "text"
        block.text = text
        self.content = [block]
        self.usage = mock.Mock(input_tokens=input_tokens,
                               output_tokens=output_tokens)


class TestAnthropicRoleRunner(unittest.TestCase):
    def _runner(self, tmp: str, responses):
        config = Config()
        config.anthropic_api_key = "test-key"
        ledger = TaskLedger(Path(tmp))
        guard = BudgetGuard(config, ledger)
        agents = load_all_agents(AGENTS_DIR)
        runner = AnthropicRoleRunner(agents["analysis"], AGENTS_DIR, config, guard)
        client = mock.Mock()
        client.messages.create.side_effect = responses
        anthropic_mod = mock.Mock()
        anthropic_mod.Anthropic.return_value = client
        return runner, anthropic_mod, client, guard

    def test_returns_result_envelope(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner, mod, client, guard = self._runner(
                tmp, [_FakeResponse('{"status": "succeeded", "summary": "done"}')])
            with mock.patch.dict(sys.modules, {"anthropic": mod}):
                result = runner.run(hello_task(), [])
            self.assertEqual(result.status, STATUS_SUCCEEDED)
            self.assertEqual(result.role, "analysis")
            usage = guard.ledger.load(result.task_id)["usage"]
            self.assertEqual(usage["total_tokens"], 150)

    def test_retries_on_malformed_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner, mod, client, _ = self._runner(tmp, [
                _FakeResponse("sorry, no json"),
                _FakeResponse('{"status": "succeeded", "summary": "retry ok"}'),
            ])
            with mock.patch.dict(sys.modules, {"anthropic": mod}):
                result = runner.run(hello_task(), [])
            self.assertEqual(result.status, STATUS_SUCCEEDED)
            self.assertEqual(client.messages.create.call_count, 2)

    def test_budget_exceeded_returns_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner, mod, client, guard = self._runner(
                tmp, [_FakeResponse('{"status": "succeeded"}')])
            runner.config.budget_usd_per_task = 0.0
            with mock.patch.dict(sys.modules, {"anthropic": mod}):
                result = runner.run(hello_task(), [])
            self.assertEqual(result.status, STATUS_BLOCKED)
            client.messages.create.assert_not_called()

    def test_runs_bounded_role_scoped_tool_loop(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Config()
            config.anthropic_api_key = "test-key"
            ledger = TaskLedger(Path(tmp) / "ledger")
            agents = load_all_agents(AGENTS_DIR)
            executor = mock.Mock()
            executor.run.return_value = mock.Mock(
                exit_code=0, stdout="ok", stderr="")
            runner = AnthropicRoleRunner(
                agents["code"], AGENTS_DIR, config, BudgetGuard(config, ledger),
                Path(tmp), lambda task: executor,
            )
            tool_use = mock.Mock(
                type="tool_use", id="call-1", input={"command": ["true"]})
            tool_use.name = "sandbox_exec"
            first = mock.Mock(
                content=[tool_use],
                usage=mock.Mock(input_tokens=10, output_tokens=5),
            )
            second = _FakeResponse(
                '{"status": "succeeded", "summary": "tool complete"}')
            client = mock.Mock()
            client.messages.create.side_effect = [first, second]
            anthropic_mod = mock.Mock()
            anthropic_mod.Anthropic.return_value = client
            with mock.patch.dict(sys.modules, {"anthropic": anthropic_mod}):
                result = runner.run(hello_task(), [])
            self.assertEqual(result.status, STATUS_SUCCEEDED)
            executor.run.assert_called_once()
            second_call = client.messages.create.call_args_list[1].kwargs
            self.assertEqual(
                second_call["messages"][-1]["content"][0]["type"], "tool_result")


class TestGitHubIssueIntake(unittest.TestCase):
    def _intake(self) -> GitHubIssueIntake:
        return GitHubIssueIntake("owner/repo", "token")

    def test_task_from_issue_with_envelope(self):
        issue = {
            "number": 7,
            "title": "Do a thing",
            "html_url": "https://github.com/owner/repo/issues/7",
            "body": 'intro\n```json\n{"goal": "custom goal", "roles": ["analysis"]}\n```',
        }
        task = self._intake()._task_from_issue(issue)
        self.assertEqual(task.task_id, "issue-7")
        self.assertEqual(task.goal, "custom goal")
        self.assertEqual(task.roles, ["analysis"])
        self.assertEqual(task.source["system"], "github-issue")

    def test_task_from_issue_default_envelope(self):
        issue = {"number": 8, "title": "Fix billing", "html_url": "u", "body": ""}
        task = self._intake()._task_from_issue(issue)
        self.assertEqual(task.goal, "Fix billing")
        self.assertEqual(task.roles, ["analysis", "design", "code", "test"])

    def test_poll_relabels_issue(self):
        intake = self._intake()
        calls = []

        def fake_request(method, url, body=None):
            calls.append((method, url, body))
            if method == "GET":
                return [{"number": 5, "title": "T", "html_url": "u", "body": ""}]
            return None

        intake._request = fake_request
        tasks = intake.poll()
        self.assertEqual(len(tasks), 1)
        methods = [c[0] for c in calls]
        self.assertIn("DELETE", methods)
        self.assertIn("POST", methods)

    def test_deliver_posts_comment_with_approval_hint(self):
        intake = self._intake()
        calls = []
        intake._request = lambda m, u, b=None: calls.append((m, u, b))
        intake.deliver(Result(task_id="issue-5", role="analysis",
                              status="awaiting_approval", summary="s"))
        method, url, body = calls[0]
        self.assertEqual(method, "POST")
        self.assertIn("/issues/5/comments", url)
        self.assertIn("/approve analysis", body["body"])

    def test_poll_approvals(self):
        intake = self._intake()
        intake._request = lambda m, u, b=None: [
            {"body": "looks good\n/approve analysis", "user": {"login": "alice"}},
            {"body": "unrelated", "user": {"login": "bob"}},
        ]
        approvals = intake.poll_approvals("issue-5")
        self.assertEqual(approvals, [{"role": "analysis", "approver": "alice"}])


class TestDockerExecutor(unittest.TestCase):
    def test_command_construction(self):
        with tempfile.TemporaryDirectory() as tmp:
            executor = DockerExecutor(Path(tmp), image="img", network=False)
            with mock.patch("harness.sandbox.subprocess.run") as run:
                run.return_value = mock.Mock(returncode=0, stdout="", stderr="")
                executor.run(["echo", "hi"], cwd="sub", timeout=5)
            cmd = run.call_args[0][0]
            self.assertEqual(cmd[:3], ["docker", "run", "--rm"])
            self.assertIn("--network", cmd)
            self.assertIn("none", cmd)
            self.assertIn("/workspace/sub", cmd)
            self.assertEqual(cmd[-2:], ["echo", "hi"])


class TestDockerSandboxExecutor(unittest.TestCase):
    def test_creates_deterministic_task_sandbox(self):
        with tempfile.TemporaryDirectory() as tmp:
            executor = DockerSandboxExecutor(Path(tmp), "Issue 12/Feature")
            responses = [
                mock.Mock(returncode=1, stdout="", stderr="not found"),
                mock.Mock(returncode=0, stdout="", stderr=""),
            ]
            with mock.patch("harness.sandbox.shutil.which", return_value="/bin/sbx"), \
                    mock.patch("harness.sandbox.subprocess.run", side_effect=responses) as run:
                result = executor.ensure()
            self.assertTrue(result.ok)
            self.assertEqual(executor.name, "agent-army-issue-12-feature")
            self.assertEqual(
                run.call_args_list[1].args[0],
                ["sbx", "create", "--name", executor.name, "shell", str(Path(tmp).resolve())],
            )

    def test_reuses_existing_sandbox(self):
        with tempfile.TemporaryDirectory() as tmp:
            executor = DockerSandboxExecutor(Path(tmp), "task")
            with mock.patch("harness.sandbox.shutil.which", return_value="/bin/sbx"), \
                    mock.patch("harness.sandbox.subprocess.run") as run:
                run.return_value = mock.Mock(returncode=0, stdout="", stderr="")
                executor.ensure()
            run.assert_called_once()
            self.assertEqual(run.call_args.args[0], ["sbx", "exec", executor.name, "true"])

    def test_rejects_escaping_working_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            executor = DockerSandboxExecutor(Path(tmp), "task")
            with mock.patch.object(executor, "ensure", return_value=mock.Mock(ok=True)):
                result = executor.run(["echo", "no"], cwd="../outside")
            self.assertEqual(result.exit_code, 2)

    def test_missing_cli_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            executor = DockerSandboxExecutor(Path(tmp), "task")
            with mock.patch("harness.sandbox.shutil.which", return_value=None):
                with self.assertRaisesRegex(RuntimeError, "not installed"):
                    executor.ensure()

    @unittest.skipUnless(
        os.environ.get("AGENT_ARMY_RUN_SBX_INTEGRATION") == "1"
        and shutil.which("sbx"),
        "set AGENT_ARMY_RUN_SBX_INTEGRATION=1 with sbx installed",
    )
    def test_live_shell_sandbox(self):
        with tempfile.TemporaryDirectory() as tmp:
            executor = DockerSandboxExecutor(
                Path(tmp), f"integration-{uuid.uuid4().hex[:10]}")
            try:
                self.assertTrue(executor.ensure(timeout=180).ok)
                result = executor.run(
                    ["sh", "-c", "printf sandbox-ok"], timeout=30)
                self.assertTrue(result.ok, result.stderr)
                self.assertEqual(result.stdout, "sandbox-ok")
            finally:
                executor.remove(timeout=60)


class TestSandboxConfig(unittest.TestCase):
    def test_docker_sandbox_environment(self):
        environment = {
            "AGENT_ARMY_SANDBOX_BACKEND": "docker-sandbox",
            "AGENT_ARMY_SANDBOX_TEMPLATE": "custom-shell",
            "AGENT_ARMY_SANDBOX_CLONE": "true",
            "AGENT_ARMY_SANDBOX_RETAIN": "1",
            "AGENT_ARMY_SANDBOX_TIMEOUT": "42",
        }
        with mock.patch.dict(os.environ, environment, clear=False):
            config = Config()
        self.assertEqual(config.sandbox_backend, "docker-sandbox")
        self.assertEqual(config.sandbox_template, "custom-shell")
        self.assertTrue(config.sandbox_clone)
        self.assertTrue(config.sandbox_retain)
        self.assertEqual(config.sandbox_timeout, 42)


class TestSandboxToolExecution(unittest.TestCase):
    def test_role_cannot_invoke_undeclared_tool(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Config()
            agents = load_all_agents(AGENTS_DIR)
            runner = AnthropicRoleRunner(
                agents["analysis"], AGENTS_DIR, config,
                BudgetGuard(config, TaskLedger(Path(tmp) / "ledger")),
                Path(tmp),
            )
            with self.assertRaisesRegex(ValueError, "not allowed"):
                runner._execute_tool(hello_task(), "sandbox_exec", {"command": ["true"]})

    def test_sandbox_exec_caps_timeout_and_redacts_secrets(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Config()
            config.anthropic_api_key = "very-secret"
            config.sandbox_timeout = 10
            agents = load_all_agents(AGENTS_DIR)
            executor = mock.Mock()
            executor.run.return_value = mock.Mock(
                exit_code=0, stdout="very-secret", stderr="")
            runner = AnthropicRoleRunner(
                agents["code"], AGENTS_DIR, config,
                BudgetGuard(config, TaskLedger(Path(tmp) / "ledger")),
                Path(tmp), lambda task: executor,
            )
            output = runner._execute_tool(
                hello_task(), "sandbox_exec",
                {"command": ["echo", "ok"], "timeout": 999},
            )
            self.assertNotIn("very-secret", output)
            self.assertIn("[REDACTED]", output)
            executor.run.assert_called_once_with(
                ["echo", "ok"], cwd=None, timeout=10)


class TestServiceLoop(unittest.TestCase):
    def test_dry_run_cycle_over_folder_intake(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            inbox = tmp_path / "inbox"
            inbox.mkdir()
            (inbox / "hello.json").write_text(HELLO_TASK.read_text(encoding="utf-8"))
            config = Config()
            config.data_dir = tmp_path
            intake = FolderIntake(inbox, tmp_path / "outbox")
            service = Service(config, intake, dry_run=True)
            service.cycle()  # runs analysis, pauses at gate
            state = service.ledger.load("hello-world-001")
            self.assertEqual(state["results"][0]["role"], "analysis")
            service.ledger.approve("hello-world-001", "analysis", "tester")
            service.ledger.approve("hello-world-001", "design", "tester")
            service.ledger.approve("hello-world-001", "test", "tester")
            service.cycle()
            completed = {r["role"] for r in
                         service.ledger.results_for("hello-world-001")
                         if r["status"] == "succeeded"}
            self.assertEqual(completed, {"analysis", "design", "code", "test"})
            self.assertNotIn("hello-world-001", service.active_tasks)

    def test_restores_incomplete_tasks_from_ledger(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            inbox = tmp_path / "inbox"
            inbox.mkdir()
            config = Config()
            config.data_dir = tmp_path
            intake = FolderIntake(inbox, tmp_path / "outbox")
            ledger = TaskLedger(tmp_path / "ledger")
            ledger.record_task(hello_task())
            ledger.record_result(Result(
                task_id="hello-world-001", role="analysis",
                status=STATUS_SUCCEEDED))
            restarted = Service(config, intake, dry_run=True)
            self.assertIn("hello-world-001", restarted.active_tasks)


if __name__ == "__main__":
    unittest.main()
