"""Nimbus Nutritionist specialist — configuration."""

from __future__ import annotations

from app.specialist.config import SpecialistConfig, SpecialistSkillSpec

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
    label='Nutritionist',
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
