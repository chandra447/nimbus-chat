from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
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

from app.checkpointing import create_sqlite_checkpointer
from app.settings import Settings
from app.specialist.builder import (
    GenericSpecialistExecutor,
    SpecialistConfig,
    build_specialist_agent_card,
)


def create_specialist_app(
    settings: Settings,
    config: SpecialistConfig,
) -> FastAPI:
    agent_card = build_specialist_agent_card(settings, config)

    engine = create_async_engine(settings.sqlite_async_url, future=True)
    task_store = DatabaseTaskStore(engine, table_name=config.tasks_table)
    push_config_store = DatabasePushNotificationConfigStore(
        engine,
        table_name=config.push_notification_table,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await task_store.initialize()
        await push_config_store.initialize()

        checkpointer_conn, checkpointer = await create_sqlite_checkpointer(settings)
        executor = GenericSpecialistExecutor(settings, config, checkpointer=checkpointer)
        request_handler = DefaultRequestHandler(
            agent_executor=executor,
            task_store=task_store,
            agent_card=agent_card,
            push_config_store=push_config_store,
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
        title=f'{config.name}',
        version=config.version,
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

    @app.get('/health')
    def healthcheck() -> dict[str, object]:
        return {
            'status': 'ok',
            'specialist_type': config.table_name_prefix,
            'specialist_public_url': settings.specialist_public_url,
            'agent_name': agent_card.name,
            'tavily_enabled': settings.tavily_enabled,
            'tavily_configured': settings.tavily_configured,
        }

    @app.get('/api/specialist/agent-card')
    def specialist_agent_card_preview() -> dict[str, object]:
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


# Backwards-compatible alias.
def create_travel_specialist_app(settings: Settings) -> FastAPI:
    from app.specialist.configs import travel_config
    return create_specialist_app(settings, travel_config)
