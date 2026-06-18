from __future__ import annotations

from a2a.types import AgentCard
from a2a.types.a2a_pb2 import AgentCapabilities, AgentInterface, AgentProvider, AgentSkill

from app.settings import Settings


def build_orchestrator_agent_card(settings: Settings) -> AgentCard:
    return AgentCard(
        name='Nimbus Orchestrator',
        description=(
            'Primary chat entrypoint for Nimbus Chat. Receives every user message, '
            'routes eligible tasks to registered specialist A2A agents, and streams '
            'status and output back to the frontend.'
        ),
        version='0.1.0',
        documentation_url=f'{settings.orchestrator_public_url}/docs',
        provider=AgentProvider(
            organization='Nimbus Chat',
            url=settings.orchestrator_public_url,
        ),
        capabilities=AgentCapabilities(
            streaming=True,
            push_notifications=False,
            extended_agent_card=False,
        ),
        default_input_modes=['text/plain'],
        default_output_modes=['text/plain'],
        supported_interfaces=[
            AgentInterface(
                url=f'{settings.orchestrator_public_url}/a2a/jsonrpc',
                protocol_binding='JSONRPC',
                protocol_version='1.0',
            ),
            AgentInterface(
                url=f'{settings.orchestrator_public_url}/a2a',
                protocol_binding='HTTP+JSON',
                protocol_version='1.0',
            ),
        ],
        skills=[
            AgentSkill(
                id='route_to_specialist',
                name='Route to registered specialist',
                description='Routes user requests to a registered specialist agent when the specialist card matches the task.',
                tags=['orchestration', 'routing', 'multi-agent'],
                examples=[
                    'Find the right specialist for this request.',
                    'Route this travel planning task to the travel agent.',
                ],
                input_modes=['text/plain'],
                output_modes=['text/plain'],
            ),
            AgentSkill(
                id='stream_chat_response',
                name='Stream staged chat response',
                description='Streams orchestration status updates and final text output back to the client.',
                tags=['streaming', 'chat'],
                examples=['Stream status updates while a specialist is working.'],
                input_modes=['text/plain'],
                output_modes=['text/plain'],
            ),
        ],
    )
