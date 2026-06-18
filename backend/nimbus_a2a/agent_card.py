"""Build an A2A ``AgentCard`` from a :class:`SpecialistConfig`."""

from __future__ import annotations

from a2a.types import AgentCard
from a2a.types.a2a_pb2 import (
    AgentCapabilities,
    AgentInterface,
    AgentProvider,
    AgentSkill,
)

from nimbus_a2a.config import SpecialistConfig


def build_agent_card(
    config: SpecialistConfig,
    *,
    public_url: str,
    internal_url: str,
) -> AgentCard:
    """Build an A2A AgentCard advertising the specialist's capabilities.

    The ``streaming`` capability reflects ``config.streaming`` — ``True`` means
    the specialist pushes incremental artifact chunks; ``False`` means it
    returns a single final response. ``push_notifications`` is always ``True``
    (the specialist works in the background after ``return_immediately=True``).
    """
    return AgentCard(
        name=config.name,
        description=config.description,
        version=config.version,
        documentation_url=f'{public_url}/docs',
        provider=AgentProvider(
            organization='Nimbus Chat',
            url=public_url,
        ),
        capabilities=AgentCapabilities(
            streaming=config.streaming,
            push_notifications=True,
            extended_agent_card=False,
        ),
        default_input_modes=['text/plain'],
        default_output_modes=['text/plain'],
        supported_interfaces=[
            AgentInterface(
                url=f'{internal_url}/a2a/jsonrpc',
                protocol_binding='JSONRPC',
                protocol_version='1.0',
            ),
            AgentInterface(
                url=f'{internal_url}/a2a',
                protocol_binding='HTTP+JSON',
                protocol_version='1.0',
            ),
        ],
        skills=[
            AgentSkill(
                id=skill.id,
                name=skill.name,
                description=skill.description,
                tags=skill.tags,
                examples=skill.examples,
                input_modes=['text/plain'],
                output_modes=['text/plain'],
            )
            for skill in config.skills
        ],
    )
