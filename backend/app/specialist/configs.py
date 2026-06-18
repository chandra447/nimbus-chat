"""Specialist configurations for travel and nutrition specialists."""

from __future__ import annotations

from nimbus_a2a import SpecialistConfig, SpecialistSkillSpec


# ---------------------------------------------------------------------------
# Travel specialist
# ---------------------------------------------------------------------------


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
    agent_name_label='Travel Planner',
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


# ---------------------------------------------------------------------------
# Nutrition specialist
# ---------------------------------------------------------------------------


nutrition_config = SpecialistConfig(
    name='Nimbus Nutritionist',
    description=(
        'A nutrition and dietetics specialist that helps with meal planning, '
        'dietary guidance, macro/micronutrient optimization, and healthy eating '
        'strategies for various goals and conditions.'
    ),
    system_prompt=(
        'You are Nimbus Nutritionist, a certified nutrition and dietetics specialist. '
        'Provide evidence-based nutrition guidance. When useful, structure the answer '
        'with sections like Nutritional Overview, Meal Plan, Macro Breakdown, Food '
        'Sources, and Practical Tips. Be transparent about assumptions (age, weight, '
        'activity level, goals) and always recommend consulting a healthcare '
        'professional for medical conditions. Use the research tool when you need '
        'current, specific information about nutrients, supplements, dietary studies, '
        'or food composition.'
    ),
    tavily_tool_name='research_nutrition',
    tavily_tool_description=(
        'Search the web for current nutrition science, dietary guidelines, food '
        'composition data, supplement research, and healthy eating evidence. Use '
        'when the user asks about specific nutrients, diet studies, calorie/macro '
        'targets, or evidence-based nutrition claims.'
    ),
    table_name_prefix='nutrition_specialist',
    artifact_name='nutrition-plan',
    agent_name_label='Nutritionist',
    streaming=False,
    skills=[
        SpecialistSkillSpec(
            id='meal_planning',
            name='Meal planning',
            description='Creates structured meal plans based on dietary goals, restrictions, and preferences.',
            tags=['nutrition', 'meal-plan', 'diet'],
            examples=[
                'Create a high-protein vegetarian meal plan for muscle gain.',
                'Plan a week of low-carb dinners under 500 calories each.',
            ],
        ),
        SpecialistSkillSpec(
            id='macro_guidance',
            name='Macro and calorie guidance',
            description='Calculates and recommends macronutrient ratios and calorie targets for body composition goals.',
            tags=['nutrition', 'macros', 'calories'],
            examples=[
                'What macros should I eat for fat loss at 180 lbs?',
                'How many calories do I need for lean bulking?',
            ],
        ),
        SpecialistSkillSpec(
            id='dietary_conditions',
            name='Dietary condition management',
            description='Advises on nutrition for specific conditions like diabetes, hypertension, or food intolerances.',
            tags=['nutrition', 'health', 'conditions'],
            examples=[
                'What foods should I avoid with prediabetes?',
                'Suggest a low-sodium meal plan for hypertension.',
            ],
        ),
        SpecialistSkillSpec(
            id='nutrient_education',
            name='Nutrient education',
            description='Explains the role of vitamins, minerals, and supplements, and identifies good food sources.',
            tags=['nutrition', 'vitamins', 'supplements'],
            examples=[
                'What are the best plant-based sources of iron?',
                'Should I take a vitamin D supplement in winter?',
            ],
        ),
    ],
)


# Registry of all specialist configs by type name.
SPECIALIST_CONFIGS: dict[str, SpecialistConfig] = {
    'travel': travel_config,
    'nutrition': nutrition_config,
}


def get_specialist_config(specialist_type: str) -> SpecialistConfig:
    """Look up a specialist config by type name (e.g. 'travel', 'nutrition')."""
    config = SPECIALIST_CONFIGS.get(specialist_type)
    if config is None:
        raise ValueError(
            f'Unknown specialist type: {specialist_type!r}. '
            f'Available: {", ".join(SPECIALIST_CONFIGS)}'
        )
    return config
