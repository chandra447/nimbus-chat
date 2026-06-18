"""App-level wiring: turn a Nimbus :class:`SpecialistConfig` into a FastAPI app.

Builds the app's chat model, a LangGraph SQLite checkpointer factory, a
:class:`LangChainSpecialistExecutor`, and an A2A ``AgentCard`` — then delegates
to the SDK's :func:`nimbus_a2a.create_specialist_app` for all A2A / push-
notification / streaming plumbing.
"""

from __future__ import annotations

from fastapi import FastAPI

from app.checkpointing import create_sqlite_checkpointer
from app.llm import build_chat_model
from app.settings import Settings
from app.specialist.agent_card import build_specialist_agent_card
from app.specialist.config import SpecialistConfig
from app.specialist.executor import LangChainSpecialistExecutor
from app.tracing import get_tracer
from nimbus_a2a import SpecialistServerConfig, create_specialist_app as create_specialist_app_sdk


def create_specialist_app(settings: Settings, config: SpecialistConfig) -> FastAPI:
    model = build_chat_model(settings, streaming=config.streaming)

    async def checkpointer_factory():
        return await create_sqlite_checkpointer(settings)

    executor = LangChainSpecialistExecutor(
        config,
        model=model,
        checkpointer_factory=checkpointer_factory,
        tavily_api_key=settings.tavily_api_key,
        tavily_enabled=settings.tavily_enabled,
    )

    agent_card = build_specialist_agent_card(
        config,
        public_url=settings.specialist_public_url,
        internal_url=settings.specialist_internal_url,
    )

    server = SpecialistServerConfig(
        db_url=settings.sqlite_async_url,
        public_url=settings.specialist_public_url,
        internal_url=settings.specialist_internal_url,
        cors_origins=settings.cors_origins,
        tasks_table=config.tasks_table,
        push_notification_table=config.push_notification_table,
    )

    tracer = get_tracer(f'specialist:{config.table_name_prefix}')
    return create_specialist_app_sdk(executor, agent_card, server=server, tracer=tracer)


# Backwards-compatible alias.
def create_travel_specialist_app(settings: Settings) -> FastAPI:
    from app.specialist.travel import travel_config
    return create_specialist_app(settings, travel_config)
