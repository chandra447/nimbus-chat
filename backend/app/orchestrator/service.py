from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from google.protobuf.json_format import ParseDict
from sqlalchemy.ext.asyncio import create_async_engine

from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes.agent_card_routes import create_agent_card_routes
from a2a.server.routes.fastapi_routes import add_a2a_routes_to_fastapi
from a2a.server.routes.jsonrpc_routes import create_jsonrpc_routes
from a2a.server.routes.rest_routes import create_rest_routes
from a2a.server.tasks import (
    DatabasePushNotificationConfigStore,
    DatabaseTaskStore,
)
from a2a.types.a2a_pb2 import StreamResponse

from app.checkpointing import create_sqlite_checkpointer
from app.orchestrator.agent_card import build_orchestrator_agent_card
from app.orchestrator.api import router as orchestrator_router
from app.orchestrator.callback import callback_manager
from app.orchestrator.executor import OrchestratorExecutor
from app.orchestrator.middleware import RegisteredSpecialistPromptMiddleware
from app.orchestrator.registry import SpecialistRegistry
from app.orchestrator.routing import OrchestratorResponder, OrchestratorRouter, OrchestratorSynthesizer
from app.settings import Settings


def create_orchestrator_app(settings: Settings) -> FastAPI:
    registry = SpecialistRegistry(settings)
    prompt_middleware = RegisteredSpecialistPromptMiddleware(registry)
    agent_card = build_orchestrator_agent_card(settings)

    engine = create_async_engine(settings.sqlite_async_url, future=True)
    task_store = DatabaseTaskStore(engine)
    push_config_store = DatabasePushNotificationConfigStore(engine)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await registry.initialize()
        await registry.refresh_all(ignore_errors=True)
        await task_store.initialize()
        await push_config_store.initialize()

        checkpointer_conn, checkpointer = await create_sqlite_checkpointer(settings)
        router = OrchestratorRouter(settings, registry, checkpointer=checkpointer)
        responder = OrchestratorResponder(settings, checkpointer=checkpointer)
        synthesizer = OrchestratorSynthesizer(settings, checkpointer=checkpointer)
        executor = OrchestratorExecutor(settings, registry, router, responder, synthesizer)
        request_handler = DefaultRequestHandler(
            agent_executor=executor,
            task_store=task_store,
            agent_card=agent_card,
            push_config_store=push_config_store,
        )

        app.state.specialist_registry = registry
        app.state.orchestrator_agent_card = agent_card
        app.state.prompt_middleware = prompt_middleware
        app.state.orchestrator_executor = executor
        app.state.orchestrator_router = router
        app.state.orchestrator_request_handler = request_handler

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
        title='Nimbus Orchestrator',
        version='0.1.0',
        lifespan=lifespan,
    )
    _cors_origins = [
        origin.strip()
        for origin in settings.cors_origins.split(',')
    ]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins,
        allow_credentials=False,
        allow_methods=['*'],
        allow_headers=['*'],
    )
    app.include_router(orchestrator_router)

    @app.get('/health')
    def healthcheck() -> dict[str, object]:
        return {
            'status': 'ok',
            'openai_base_url': settings.openai_base_url,
            'openai_model': settings.openai_model,
            'sqlite_path': str(settings.sqlite_absolute_path),
            'orchestrator_public_url': settings.orchestrator_public_url,
            'specialist_public_url': settings.specialist_public_url,
            'tavily_enabled': settings.tavily_enabled,
            'tavily_configured': settings.tavily_configured,
            'specialist_card_refresh_ttl_seconds': settings.specialist_card_refresh_ttl_seconds,
            'orchestrator_agent_card_name': agent_card.name,
            'async_specialist_mode': settings.async_specialist_mode,
        }

    # ------------------------------------------------------------------
    # A2A Push-Notification Webhook
    # ------------------------------------------------------------------
    # When async_specialist_mode is enabled, specialists POST task events
    # (status updates, artifact chunks) to this endpoint. The CallbackManager
    # routes them to the corresponding executor's asyncio.Queue.
    #
    # The body is a JSON-serialised StreamResponse proto. The token in the
    # X-A2A-Notification-Token header identifies which callback queue to use.
    @app.post('/a2a/callback')
    async def specialist_push_notification(request: Request) -> dict[str, str]:
        token = request.headers.get('X-A2A-Notification-Token', '')
        body = await request.json()
        stream_response = ParseDict(body, StreamResponse())
        await callback_manager.push_event(token, stream_response)
        return {'status': 'ok'}

    @app.get('/api/orchestrator/agent-card')
    def orchestrator_agent_card_preview() -> dict[str, object]:
        return {
            'name': agent_card.name,
            'description': agent_card.description,
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
