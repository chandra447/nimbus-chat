"""nimbus_a2a — a small SDK for building A2A specialists.

Define a specialist from a :class:`SpecialistConfig` (identity, system prompt,
skills, streaming mode), then turn it into a running A2A server with
:func:`create_specialist_app`. The SDK handles the A2A server setup, the push
notification sender, task store, agent card, and the streaming/non-streaming
execution — so adding a new specialist is just adding a config.

Example::

    from nimbus_a2a import (
        SpecialistConfig,
        SpecialistSkillSpec,
        SpecialistServerConfig,
        create_specialist_app,
    )

    config = SpecialistConfig(
        name='Nimbus Travel Planner',
        description='Travel planning specialist.',
        system_prompt='You are Nimbus Travel Planner…',
        streaming=True,                 # push incremental chunks
        skills=[SpecialistSkillSpec(id='itinerary', name='Itinerary', …)],
        tavily_tool_name='research_travel',
        tavily_tool_description='Search for current travel info.',
        table_name_prefix='travel_specialist',
        artifact_name='travel-plan',
    )

    server = SpecialistServerConfig(
        db_url='sqlite+aiosqlite:///./data/nimbus-chat.db',
        public_url='http://localhost:8001',
        internal_url='http://travel-specialist:8001',
        cors_origins='*',
        tavily_api_key='tvly-…',
        tavily_enabled=True,
    )

    app = create_specialist_app(config, server, model=model, checkpointer=cp)
    # uvicorn.run(app, host='0.0.0.0', port=8001)

The push-notification / ``return_immediately`` pattern is handled for you: when
the orchestrator submits a task with ``return_immediately=True`` and a push
config, the specialist processes in the background and POSTs status/artifact
events to the orchestrator's webhook. Streaming specialists push buffered
artifact chunks live; non-streaming specialists push one final artifact.
"""

from nimbus_a2a.agent_card import build_agent_card
from nimbus_a2a.config import SpecialistConfig, SpecialistSkillSpec
from nimbus_a2a.executor import SpecialistExecutor
from nimbus_a2a.server import SpecialistServerConfig, create_specialist_app
from nimbus_a2a.tools import build_tavily_research_tool

__all__ = [
    'SpecialistConfig',
    'SpecialistSkillSpec',
    'SpecialistServerConfig',
    'SpecialistExecutor',
    'create_specialist_app',
    'build_agent_card',
    'build_tavily_research_tool',
]
