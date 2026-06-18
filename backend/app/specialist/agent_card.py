"""Build an A2A ``AgentCard`` from an app-level :class:`SpecialistConfig`.

The SDK does not build agent cards — specialist teams own their cards. This
helper is the Nimbus Chat app's convenience builder for its LangChain
specialists.
"""

from __future__ import annotations

from a2a.types import AgentCard
from a2a.types.a2a_pb2 import (
    AgentCapabilities,
    AgentInterface,
    AgentProvider,
    AgentSkill,
)

from app.specialist.config import SpecialistConfig


def build_specialist_agent_card(
    config: SpecialistConfig,
    *,
    public_url: str,
    internal_url: str,
) -> AgentCard:
    """Build an A2A AgentCard for a Nimbus specialist.

    Note: the ``streaming`` capability is set from ``config.streaming`` here,
    but the SDK's :func:`create_specialist_app` will reconcile it against the
    executor's ``streaming`` flag (the executor is the source of truth).
    ``push_notifications`` is always ``True``.
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
