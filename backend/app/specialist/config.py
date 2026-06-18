"""Shared specialist config dataclasses (app-level).

These are the Nimbus Chat app's own types, used by the shared LangChain
specialist framework. The framework-agnostic :mod:`nimbus_a2a` SDK knows
nothing about them.
"""

from __future__ import annotations

from dataclasses import dataclass, field


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
    """Definition of a Nimbus LangChain + Tavily specialist."""

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
    label: str = ''

    @property
    def tasks_table(self) -> str:
        return f'{self.table_name_prefix}_tasks'

    @property
    def push_notification_table(self) -> str:
        return f'{self.table_name_prefix}_push_notification_configs'
