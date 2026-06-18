"""Assemble a specialist into a running FastAPI + A2A server.

``create_specialist_app`` is the single entry point: give it a
:class:`SpecialistConfig`, a :class:`SpecialistServerConfig` (runtime wiring),
a chat model, and a LangGraph checkpointer — it returns a FastAPI app with:

- A2A JSON-RPC + HTTP+JSON routes (``/a2a/jsonrpc``, ``/a2a``)
- Agent card route (``/.well-known/agent-card.json``)
- ``BasePushNotificationSender`` wired in (so ``return_immediately=True``
  requests trigger background push notifications to the orchestrator webhook)
- ``/health`` and ``/api/specialist/agent-card`` endpoints
- CORS

Everything an app needs to spin up N specialists is: a config, a model, a
checkpointer, and server settings.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import httpx
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes.agent_card_routes import create_agent_card_routes
from a2a.server.routes.fastapi_routes import add_a2a_routes_to_fastapi
from a2a.server.routes.jsonrpc_routes import create_jsonrpc_routes
from a2a.server.routes.rest_routes import create_rest_routes
from a2a.server.tasks import (
    DatabasePushNotificationConfigStore,
    DatabaseTaskStore,
)
from a2a.server.tasks.base_push_notification_sender import (
    BasePushNotificationSender,
)
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.ext.asyncio import create_async_engine

from nimbus_a2a.agent_card import build_agent_card
from nimbus_a2a.config import SpecialistConfig
from nimbus_a2a.executor import SpecialistExecutor


@dataclass
class SpecialistServerConfig:
    """Runtime/server wiring for a specialist (independent of the agent def)."""

    db_url: str
    public_url: str
    internal_url: str
    cors_origins: str = '*'
    tavily_api_key: str = ''
    tavily_enabled: bool = False


def create_specialist_app(
    config: SpecialistConfig,
    server: SpecialistServerConfig,
    *,
    model: Any,
    checkpointer_factory: Any,
) -> FastAPI:
    """Build a FastAPI app running the specialist as an A2A server.

    Args:
        config: The specialist definition (name, prompt, skills, streaming…).
        server: Runtime wiring (DB url, URLs, CORS, Tavily).
        model: A LangChain chat model (already configured with API keys).
        checkpointer_factory: An async callable ``() -> (connection, checkpointer)``
            invoked inside the app lifespan. The SDK closes ``connection`` on shutdown.
    """
    agent_card = build_agent_card(
        config,
        public_url=server.public_url,
        internal_url=server.internal_url,
    )

    engine = create_async_engine(server.db_url, future=True)
    task_store = DatabaseTaskStore(engine, table_name=config.tasks_table)
    push_config_store = DatabasePushNotificationConfigStore(
        engine,
        table_name=config.push_notification_table,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await task_store.initialize()
        await push_config_store.initialize()

        checkpointer_conn, checkpointer = await checkpointer_factory()
        executor = SpecialistExecutor(
            config,
            model=model,
            checkpointer=checkpointer,
            tavily_api_key=server.tavily_api_key,
            tavily_enabled=server.tavily_enabled,
        )
        push_sender = BasePushNotificationSender(
            httpx_client=httpx.AsyncClient(timeout=30.0),
            config_store=push_config_store,
        )
        request_handler = DefaultRequestHandler(
            agent_executor=executor,
            task_store=task_store,
            agent_card=agent_card,
            push_config_store=push_config_store,
            push_sender=push_sender,
        )

        app.state.specialist_agent_card = agent_card
        app.state.specialist_executor = executor
        app.state.specialist_request_handler = request_handler

        add_a2a_routes_to_fastapi(
            app,
            agent_card_routes=create_agent_card_routes(agent_card),
            jsonrpc_routes=create_jsonrpc_routes(
                request_handler,
                rpc_url='/a2a/jsonrpc',
            ),
            rest_routes=create_rest_routes(request_handler, path_prefix='/a2a'),
        )

        try:
            yield
        finally:
            await checkpointer_conn.close()
            await engine.dispose()

    app = FastAPI(
        title=config.name,
        version=config.version,
        lifespan=lifespan,
    )
    _cors_origins = [origin.strip() for origin in server.cors_origins.split(',')]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins,
        allow_credentials=False,
        allow_methods=['*'],
        allow_headers=['*'],
    )

    @app.get('/health')
    def healthcheck() -> dict[str, object]:
        return {
            'status': 'ok',
            'specialist_type': config.table_name_prefix,
            'specialist_name': agent_card.name,
            'streaming': config.streaming,
            'public_url': server.public_url,
            'tavily_enabled': server.tavily_enabled,
            'tavily_configured': server.tavily_enabled and bool(server.tavily_api_key),
        }

    @app.get('/api/specialist/agent-card')
    def specialist_agent_card_preview() -> dict[str, object]:
        return {
            'name': agent_card.name,
            'description': agent_card.description,
            'streaming': config.streaming,
            'push_notifications': True,
            'skills': [
                {
                    'id': skill.id,
                    'name': skill.name,
                    'description': skill.description,
                    'tags': list(skill.tags),
                    'examples': list(skill.examples),
                }
                for skill in agent_card.skills
            ],
        }

    return app
