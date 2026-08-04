"""Agent army harness: intake, orchestration, role runners, sandbox layer."""
from .models import Task, Result, Artifact, AgentDefinition, ROLES
from .agent_loader import load_agent_definition, load_all_agents, build_system_prompt
from .intake import IntakeAdapter, FolderIntake
from .ledger import TaskLedger
from .orchestrator import Orchestrator
from .runners import RoleRunner, PromptRoleRunner, NullRoleRunner
from .sandbox import SandboxExecutor, LocalExecutor, E2BExecutor, GitHubRunnerExecutor

__all__ = [
    "Task", "Result", "Artifact", "AgentDefinition", "ROLES",
    "load_agent_definition", "load_all_agents", "build_system_prompt",
    "IntakeAdapter", "FolderIntake",
    "TaskLedger", "Orchestrator",
    "RoleRunner", "PromptRoleRunner", "NullRoleRunner",
    "SandboxExecutor", "LocalExecutor", "E2BExecutor", "GitHubRunnerExecutor",
]
