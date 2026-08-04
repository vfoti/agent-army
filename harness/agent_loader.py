"""Loader for structured agent definitions (agents/<role>/<role>.agent.md).

Parses YAML-style frontmatter without external dependencies. Supports the
subset of YAML used by the agent definitions: scalar values, one level of
nesting, and lists of scalars.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Tuple

from .models import AgentDefinition, ROLES


class _Pending:
    """Placeholder that becomes a dict or list when the first child arrives."""

    def __init__(self, parent: Any, key: str) -> None:
        self.parent = parent
        self.key = key
        self.value: Any = None

    def _materialize(self, as_list: bool) -> Any:
        if self.value is None:
            self.value = [] if as_list else {}
            if isinstance(self.parent, _Pending):
                self.parent[self.key] = self.value
            else:
                self.parent[self.key] = self.value
        return self.value

    def append(self, item: Any) -> None:
        self._materialize(as_list=True).append(item)

    def __setitem__(self, key: str, value: Any) -> None:
        self._materialize(as_list=False)[key] = value


def _scalar(value: str) -> Any:
    if value == "null":
        return None
    if value == "true":
        return True
    if value == "false":
        return False
    return value.strip("'\"")


def _accepts(container: Any, want_list: bool) -> bool:
    if isinstance(container, _Pending):
        return True
    return isinstance(container, list) if want_list else isinstance(container, dict)


def _parse_frontmatter(text: str) -> Dict[str, Any]:
    """Parse the minimal-YAML frontmatter used in agent definition files."""
    data: Dict[str, Any] = {}
    stack: List[Tuple[int, Any]] = [(-1, data)]
    for raw in text.splitlines():
        if not raw.strip() or raw.strip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip())
        line = raw.strip()
        while len(stack) > 1 and indent <= stack[-1][0]:
            stack.pop()
        container = stack[-1][1]
        if line.startswith("- "):
            if not _accepts(container, want_list=True):
                raise ValueError(f"list item outside a list: {line}")
            container.append(_scalar(line[2:]))
        else:
            key, _, value = line.partition(":")
            key = key.strip()
            value = value.strip()
            if not _accepts(container, want_list=False):
                raise ValueError(f"mapping entry inside a list: {line}")
            if value:
                container[key] = _scalar(value)
            else:
                stack.append((indent, _Pending(container, key)))
    return data


def load_agent_definition(path: Path) -> AgentDefinition:
    """Load a single agent definition file with frontmatter."""
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        raise ValueError(f"{path}: missing frontmatter")
    _, frontmatter, prompt = text.split("---", 2)
    meta = _parse_frontmatter(frontmatter)
    role = meta.get("role")
    if role not in ROLES:
        raise ValueError(f"{path}: unknown role {role!r}")
    return AgentDefinition(
        name=meta.get("name", path.stem),
        role=role,
        description=meta.get("description", ""),
        tools=meta.get("tools", []),
        inputs=meta.get("inputs", {}),
        outputs=meta.get("outputs", {}),
        handoff=meta.get("handoff", {}),
        subagents=meta.get("subagents", []),
        shared_instructions=meta.get("shared_instructions"),
        prompt=prompt.strip(),
    )


def load_all_agents(agents_dir: Path) -> Dict[str, AgentDefinition]:
    """Load every role definition under agents/<role>/<role>.agent.md."""
    agents: Dict[str, AgentDefinition] = {}
    for role in ROLES:
        path = agents_dir / role / f"{role}.agent.md"
        if path.exists():
            agents[role] = load_agent_definition(path)
    return agents


def build_system_prompt(agent: AgentDefinition, agents_dir: Path) -> str:
    """Compose the role prompt with the shared performance instructions."""
    parts: List[str] = []
    if agent.shared_instructions:
        shared = (agents_dir / agent.role / agent.shared_instructions).resolve()
        if shared.exists():
            parts.append(shared.read_text(encoding="utf-8").strip())
    parts.append(agent.prompt)
    return "\n\n".join(parts)
