"""Specialist configuration types.

A :class:`SpecialistConfig` fully describes a specialist agent — its identity,
system prompt, skills, tool wiring, and whether it streams its output or
returns a single final response. The SDK turns a config into a running A2A
server (see :mod:`nimbus_a2a.server`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class SpecialistSkillSpec:
    """One advertised skill on the specialist's A2A agent card."""

    id: str
    name: str
    description: str
    tags: list[str] = field(default_factory=list)
    examples: list[str] = field(default_factory=list)


@dataclass
class SpecialistConfig:
    """Definition of a specialist agent.

    Attributes:
        streaming: When ``True`` (default), the specialist pushes incremental
            artifact chunks to the orchestrator webhook as it generates — giving
            a live activity-trail preview. When ``False``, it accumulates the
            full response and pushes **one** final artifact on completion
            (fewer push notifications, no live preview). Advertised via the
            ``streaming`` capability on the agent card.
        extra_tools: Additional LangChain tools beyond the Tavily research tool.
        agent_factory: Optional callable ``(config, tools, checkpointer) -> agent``
            to build a custom agent. Defaults to ``create_agent`` with the
            config's system prompt.
    """

    name: str
    description: str
    system_prompt: str = ''
    version: str = '0.1.0'
    streaming: bool = True
    skills: list[SpecialistSkillSpec] = field(default_factory=list)
    tavily_tool_name: str = 'research_web'
    tavily_tool_description: str = 'Search the web for up-to-date information.'
    table_name_prefix: str = 'specialist'
    artifact_name: str = 'specialist-response'
    agent_name_label: str = ''
    extra_tools: list[Any] = field(default_factory=list)
    agent_factory: Callable[..., Any] | None = None

    @property
    def tasks_table(self) -> str:
        return f'{self.table_name_prefix}_tasks'

    @property
    def push_notification_table(self) -> str:
        return f'{self.table_name_prefix}_push_notification_configs'

    @property
    def label(self) -> str:
        """Human-friendly label used in status messages."""
        return self.agent_name_label or self.name
