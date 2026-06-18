from __future__ import annotations

from a2a.types import AgentCard
from a2a.types.a2a_pb2 import AgentCapabilities, AgentInterface, AgentProvider, AgentSkill

from app.settings import Settings


def build_travel_specialist_agent_card(settings: Settings) -> AgentCard:
    # Interfaces should be reachable by the orchestrator. In Docker the
    # orchestrator resolves the specialist service by its internal URL, so
    # agent-card interface URLs use that. The provider URL remains the public
    # identity URL.
    interface_base_url = settings.specialist_internal_url
    return AgentCard(
        name='Nimbus Travel Planner',
        description=(
            'A travel-planning specialist that helps with destination research, '
            'itinerary creation, budget-aware planning, and activity recommendations.'
        ),
        version='0.1.0',
        documentation_url=f'{settings.specialist_public_url}/docs',
        provider=AgentProvider(
            organization='Nimbus Chat',
            url=settings.specialist_public_url,
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
                url=f'{interface_base_url}/a2a/jsonrpc',
                protocol_binding='JSONRPC',
                protocol_version='1.0',
            ),
            AgentInterface(
                url=f'{interface_base_url}/a2a',
                protocol_binding='HTTP+JSON',
                protocol_version='1.0',
            ),
        ],
        skills=[
            AgentSkill(
                id='destination_planning',
                name='Destination planning',
                description='Recommends destinations based on season, budget, trip length, and travel style.',
                tags=['travel', 'destination', 'planning'],
                examples=[
                    'Where should I go in Europe in October for a romantic trip?',
                    'Suggest warm places for a 5-day budget trip in February.',
                ],
                input_modes=['text/plain'],
                output_modes=['text/plain'],
            ),
            AgentSkill(
                id='itinerary_creation',
                name='Itinerary creation',
                description='Builds day-by-day itineraries with pacing, neighborhood suggestions, and logistics notes.',
                tags=['travel', 'itinerary'],
                examples=[
                    'Plan a 4-day Tokyo itinerary for first-time visitors.',
                    'Create a 7-day Italy itinerary with Rome and Florence.',
                ],
                input_modes=['text/plain'],
                output_modes=['text/plain'],
            ),
            AgentSkill(
                id='budget_travel_advice',
                name='Budget travel advice',
                description='Suggests travel plans that fit a given budget, including rough cost breakdowns.',
                tags=['travel', 'budget'],
                examples=[
                    'Plan a Bali trip under $1200 including hotels and food.',
                    'How can I spend 3 days in New York on a tight budget?',
                ],
                input_modes=['text/plain'],
                output_modes=['text/plain'],
            ),
            AgentSkill(
                id='activity_recommendations',
                name='Activity recommendations',
                description='Recommends activities, neighborhoods, food spots, and experiences based on preferences.',
                tags=['travel', 'activities', 'recommendations'],
                examples=[
                    'What are fun family activities in Singapore?',
                    'Recommend foodie experiences in Mexico City.',
                ],
                input_modes=['text/plain'],
                output_modes=['text/plain'],
            ),
        ],
    )
