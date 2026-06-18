"""Assemble a specialist into a running FastAPI + A2A server.

This is the single entry point for specialist teams. A team provides:

1. Their :class:`SpecialistExecutor` subclass instance (agent logic built with
   any framework, ``streaming`` declared).
2. Their A2A ``AgentCard`` (name, description, skills, interfaces — the team's
   to maintain).

…and gets back a FastAPI app with the A2A JSON-RPC + HTTP+JSON routes, the
agent card route, the ``BasePushNotificationSender`` wired in (so
``return_immediately=True`` requests trigger background push notifications),
``/health``, and CORS. The SDK patches the card's ``streaming`` capability to
match ``executor.streaming`` so there's one source of truth.

Everything an app needs to spin up a specialist is: an executor, a card, and a
:class:`SpecialistServerConfig`.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass

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

from nimbus_a2a.executor import SpecialistExecutor


@dataclass
class SpecialistServerConfig:
    """Runtime/server wiring for a specialist (independent of agent logic).

    Held separate from the agent definition so the same executor/card can be
    deployed against different environments.
    """

    db_url: str
    public_url: str
    internal_url: str
    cors_origins: str = '*'
    tasks_table: str = 'specialist_tasks'
    push_notification_table: str = 'specialist_push_notification_configs'


def create_specialist_app(
    executor: SpecialistExecutor,
    agent_card,
    *,
    server: SpecialistServerConfig,
) -> FastAPI:
    """Build a FastAPI app running the specialist as an A2A server.

    Args:
        executor: The team's :class:`SpecialistExecutor` instance (agent logic
            + ``streaming`` mode).
        agent_card: The team's A2A ``AgentCard``. Its ``streaming`` capability
            is patched to match ``executor.streaming``.
        server: Runtime wiring (DB url, public/internal URLs, CORS, table names).
    """
    # One source of truth: the executor declares streaming; reflect it on the card.
    agent_card.capabilities.streaming = executor.streaming

    engine = create_async_engine(server.db_url, future=True)
    task_store = DatabaseTaskStore(engine, table_name=server.tasks_table)
    push_config_store = DatabasePushNotificationConfigStore(
        engine,
        table_name=server.push_notification_table,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await task_store.initialize()
        await push_config_store.initialize()
        await executor.startup()

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
            await executor.shutdown()
            await engine.dispose()

    app = FastAPI(
        title=getattr(agent_card, 'name', 'Nimbus Specialist'),
        version=getattr(agent_card, 'version', '0.1.0'),
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
            'specialist_name': getattr(agent_card, 'name', 'specialist'),
            'streaming': executor.streaming,
            'public_url': server.public_url,
        }

    @app.get('/api/specialist/agent-card')
    def specialist_agent_card_preview() -> dict[str, object]:
        skills = list(getattr(agent_card, 'skills', []) or [])
        return {
            'name': getattr(agent_card, 'name', ''),
            'description': getattr(agent_card, 'description', ''),
            'streaming': executor.streaming,
            'push_notifications': True,
            'skills': [
                {
                    'id': getattr(s, 'id', ''),
                    'name': getattr(s, 'name', ''),
                    'description': getattr(s, 'description', ''),
                    'tags': list(getattr(s, 'tags', []) or []),
                    'examples': list(getattr(s, 'examples', []) or []),
                }
                for s in skills
            ],
        }

    return app
