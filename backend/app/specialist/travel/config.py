"""Nimbus Travel Planner specialist — configuration."""

from __future__ import annotations

from app.specialist.config import SpecialistConfig, SpecialistSkillSpec

travel_config = SpecialistConfig(
    name='Nimbus Travel Planner',
    description=(
        'A travel-planning specialist that helps with destination research, '
        'itinerary creation, budget-aware planning, and activity recommendations.'
    ),
    system_prompt=(
        'You are Nimbus Travel Planner, a travel specialist agent. '
        'Provide practical travel help. When useful, structure the answer '
        'with sections like Overview, Suggested Itinerary, Budget Notes, '
        'Activities, and Travel Tips. Be transparent when making assumptions. '
        'Use the research tool when you need current, specific information about '
        'destinations, prices, or travel conditions.'
    ),
    tavily_tool_name='research_travel',
    tavily_tool_description=(
        'Search the web for current travel information, destination guides, prices, '
        'and recommendations. Use when the user asks about specific destinations, '
        'current prices, travel conditions, hotels, flights, or attractions.'
    ),
    table_name_prefix='travel_specialist',
    artifact_name='travel-plan',
    label='Travel Planner',
    streaming=True,
    skills=[
        SpecialistSkillSpec(
            id='destination_planning',
            name='Destination planning',
            description='Recommends destinations based on season, budget, trip length, and travel style.',
            tags=['travel', 'destination', 'planning'],
            examples=[
                'Where should I go in Europe in October for a romantic trip?',
                'Suggest warm places for a 5-day budget trip in February.',
            ],
        ),
        SpecialistSkillSpec(
            id='itinerary_creation',
            name='Itinerary creation',
            description='Builds day-by-day itineraries with pacing, neighborhood suggestions, and logistics notes.',
            tags=['travel', 'itinerary'],
            examples=[
                'Plan a 4-day Tokyo itinerary for first-time visitors.',
                'Create a 7-day Italy itinerary with Rome and Florence.',
            ],
        ),
        SpecialistSkillSpec(
            id='budget_travel_advice',
            name='Budget travel advice',
            description='Suggests travel plans that fit a given budget, including rough cost breakdowns.',
            tags=['travel', 'budget'],
            examples=[
                'Plan a Bali trip under $1200 including hotels and food.',
                'How can I spend 3 days in New York on a tight budget?',
            ],
        ),
        SpecialistSkillSpec(
            id='activity_recommendations',
            name='Activity recommendations',
            description='Recommends activities, neighborhoods, food spots, and experiences based on preferences.',
            tags=['travel', 'activities', 'recommendations'],
            examples=[
                'What are fun family activities in Singapore?',
                'Recommend foodie experiences in Mexico City.',
            ],
        ),
    ],
)
