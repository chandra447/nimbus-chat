"""App-level wiring that turns a SpecialistConfig into a running server.

This is a thin adapter: it builds the app's chat model and a
:class:`SpecialistServerConfig` from the app settings, then delegates to
``nimbus_a2a.create_specialist_app``. All A2A / push-notification / streaming
plumbing lives in the SDK.
"""

from __future__ import annotations

from fastapi import FastAPI

from app.checkpointing import create_sqlite_checkpointer
from app.llm import build_chat_model
from app.settings import Settings
from nimbus_a2a import SpecialistConfig, SpecialistServerConfig
from nimbus_a2a.server import create_specialist_app as _create_specialist_app_sdk


def create_specialist_app(settings: Settings, config: SpecialistConfig) -> FastAPI:
    model = build_chat_model(settings, streaming=config.streaming)

    async def checkpointer_factory():
        return await create_sqlite_checkpointer(settings)

    server = SpecialistServerConfig(
        db_url=settings.sqlite_async_url,
        public_url=settings.specialist_public_url,
        internal_url=settings.specialist_internal_url,
        cors_origins=settings.cors_origins,
        tavily_api_key=settings.tavily_api_key,
        tavily_enabled=settings.tavily_enabled,
    )

    return _create_specialist_app_sdk(
        config,
        server,
        model=model,
        checkpointer_factory=checkpointer_factory,
    )


# Backwards-compatible alias.
def create_travel_specialist_app(settings: Settings) -> FastAPI:
    from app.specialist.configs import travel_config
    return create_specialist_app(settings, travel_config)
