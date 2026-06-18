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

import logging
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
from a2a.types.a2a_pb2 import TaskPushNotificationConfig
from a2a.utils.proto_utils import to_stream_response
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from google.protobuf.json_format import MessageToDict
from sqlalchemy.ext.asyncio import create_async_engine

from nimbus_a2a.executor import SpecialistExecutor

logger = logging.getLogger(__name__)


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
    tracer=None,
) -> FastAPI:
    """Build a FastAPI app running the specialist as an A2A server.

    Args:
        executor: The team's :class:`SpecialistExecutor` instance (agent logic
            + ``streaming`` mode).
        agent_card: The team's A2A ``AgentCard``. Its ``streaming`` capability
            is patched to match ``executor.streaming``.
        server: Runtime wiring (DB url, public/internal URLs, CORS, table names).
        tracer: Optional HoneyHive tracer for distributed tracing. When
            provided, a middleware extracts the W3C trace context + session
            baggage from incoming A2A request headers so the specialist's
            spans become children of the orchestrator's dispatch span, all in
            the same HoneyHive session. ``None`` disables tracing.
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

        push_sender = _TracingPushNotificationSender(
            httpx_client=httpx.AsyncClient(timeout=30.0),
            config_store=push_config_store,
            tracer=tracer,
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
            if tracer is not None:
                try:
                    tracer.flush()
                except Exception:  # noqa: BLE001
                    pass

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

    if tracer is not None:
        _add_distributed_trace_middleware(app, tracer)

    @app.get('/health')
    def healthcheck() -> dict[str, object]:
        return {
            'status': 'ok',
            'specialist_name': getattr(agent_card, 'name', 'specialist'),
            'streaming': executor.streaming,
            'public_url': server.public_url,
            'tracing_enabled': tracer is not None,
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


def _add_distributed_trace_middleware(app: FastAPI, tracer) -> None:
    """Extract incoming W3C trace context so specialist spans link to the caller.

    Wraps every request in :func:`with_distributed_trace_context`, which reads
    the ``traceparent`` + HoneyHive session/project baggage from the incoming
    A2A request headers and attaches them as the current OTel context. All
    spans created while handling the request (the specialist's LangChain agent
    spans, auto-instrumented via openinference-langchain) then become children
    of the orchestrator's ``call_specialist`` span and land in the same
    HoneyHive session.
    """
    from honeyhive.tracer.processing.context import with_distributed_trace_context
    from starlette.requests import Request

    @app.middleware('http')
    async def distributed_trace_middleware(request: Request, call_next):
        headers = {k: v for k, v in request.headers.items()}
        with with_distributed_trace_context(headers, tracer):
            return await call_next(request)


class _TracingPushNotificationSender(BasePushNotificationSender):
    """Push sender that propagates the trace context in callback headers.

    The A2A request handler dispatches push notifications from a producer task
    that inherits the OTel context attached by the trace middleware (the
    session_id + traceparent extracted from the orchestrator's original A2A
    request). By injecting that context into the callback POST headers, the
    orchestrator's ``/a2a/callback`` webhook can attach its resume processing
    (relaying chunks, resuming the graph interrupt) to the same trace +
    session as the original orchestrator turn — closing the loop on the
    push-notification/callback pattern. Without this, the callback lands in a
    separate trace.

    Falls back to plain behaviour (no extra headers) when tracing is inactive.
    """

    def __init__(self, httpx_client, config_store, tracer) -> None:
        super().__init__(httpx_client=httpx_client, config_store=config_store)
        self._tracer = tracer

    async def _dispatch_notification(
        self,
        event,
        push_info: TaskPushNotificationConfig,
        task_id: str,
    ) -> bool:
        url = push_info.url
        try:
            headers = {}
            if push_info.token:
                headers['X-A2A-Notification-Token'] = push_info.token
            # Inject W3C traceparent + HoneyHive session baggage so the
            # orchestrator's webhook resumes within the same trace + session.
            if self._tracer is not None:
                try:
                    from honeyhive.tracer.processing.context import (
                        inject_context_into_carrier,
                    )

                    inject_context_into_carrier(headers, self._tracer)
                except Exception:  # noqa: BLE001 - never break the callback
                    logger.debug(
                        'Failed to inject trace context into push notification',
                        exc_info=True,
                    )
            response = await self._client.post(
                url,
                json=MessageToDict(to_stream_response(event)),
                headers=headers,
            )
            response.raise_for_status()
            logger.info(
                'Push-notification sent for task_id=%s to URL: %s', task_id, url
            )
        except Exception:
            logger.exception(
                'Error sending push-notification for task_id=%s to URL: %s.',
                task_id,
                url,
            )
            return False
        return True
