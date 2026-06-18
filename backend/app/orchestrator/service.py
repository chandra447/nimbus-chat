"""The orchestrator FastAPI application.

The orchestrator is **not** an A2A server. It exposes:

- ``POST /api/chat`` — a Server-Sent Events stream. The frontend posts a user
  message + ``context_id``; the orchestrator runs its LangGraph StateGraph and
  streams status / token / specialist events back as SSE.
- ``POST /a2a/callback`` — the push-notification webhook. Specialists POST
  ``StreamResponse`` events here; the session relays them to the frontend and
  resumes the graph (via ``Command(resume=...)``) when a specialist completes.
- ``/api/orchestrator/*`` — REST endpoints for registering/managing specialists.

The graph itself (see ``graph.py``) uses LangGraph interrupts to pause while
specialists work, and the driver loop (see ``session.py``) bridges push
notifications back into graph resumes.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from google.protobuf.json_format import ParseDict
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import create_async_engine

from a2a.types.a2a_pb2 import StreamResponse

from app.checkpointing import create_sqlite_checkpointer
from app.orchestrator.api import router as orchestrator_router
from app.orchestrator.graph import build_orchestrator_graph
from app.orchestrator.registry import SpecialistRegistry
from app.orchestrator.routing import (
    OrchestratorResponder,
    OrchestratorRouter,
    OrchestratorSynthesizer,
)
from app.orchestrator.session import GraphSession, session_registry
from app.settings import Settings


class ChatRequest(BaseModel):
    message: str
    context_id: str


def create_orchestrator_app(settings: Settings) -> FastAPI:
    registry = SpecialistRegistry(settings)
    engine = create_async_engine(settings.sqlite_async_url, future=True)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await registry.initialize()
        await registry.refresh_all(ignore_errors=True)

        checkpointer_conn, checkpointer = await create_sqlite_checkpointer(settings)
        router = OrchestratorRouter(settings, registry, checkpointer=checkpointer)
        responder = OrchestratorResponder(settings, checkpointer=checkpointer)
        synthesizer = OrchestratorSynthesizer(settings, checkpointer=checkpointer)
        graph = build_orchestrator_graph(
            router=router,
            responder=responder,
            synthesizer=synthesizer,
            checkpointer=checkpointer,
        )

        app.state.specialist_registry = registry
        app.state.orchestrator_graph = graph
        app.state.orchestrator_responder = responder
        app.state.checkpointer_conn = checkpointer_conn

        try:
            yield
        finally:
            await checkpointer_conn.close()
            await engine.dispose()

    app = FastAPI(
        title='Nimbus Orchestrator',
        version='0.2.0',
        lifespan=lifespan,
    )
    _cors_origins = [origin.strip() for origin in settings.cors_origins.split(',')]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins,
        allow_credentials=False,
        allow_methods=['*'],
        allow_headers=['*'],
    )
    app.include_router(orchestrator_router)

    # ------------------------------------------------------------------
    # Chat (SSE)
    # ------------------------------------------------------------------
    @app.post('/api/chat')
    async def chat(request: Request, payload: ChatRequest) -> StreamingResponse:
        graph = request.app.state.orchestrator_graph
        responder = request.app.state.orchestrator_responder

        # Fresh parent-graph thread per turn (conversation memory lives in the
        # nested agents, keyed by context_id).
        parent_thread_id = f'{payload.context_id}:orch:{uuid4()}'
        config: dict[str, Any] = {
            'configurable': {'thread_id': parent_thread_id},
            'recursion_limit': 15,
        }

        session = GraphSession(
            graph=graph,
            config=config,
            settings=settings,
            responder=responder,
            user_input=payload.message,
            context_id=payload.context_id,
            registry=session_registry,
        )
        session.start()

        async def event_stream():
            try:
                while True:
                    event = await session.output_queue.get()
                    if event is None:
                        break
                    yield f'data: {json.dumps(event)}\n\n'
            except asyncio.CancelledError:  # client disconnected
                raise

        return StreamingResponse(
            event_stream(),
            media_type='text/event-stream',
            headers={
                'Cache-Control': 'no-cache',
                'Connection': 'keep-alive',
                'X-Accel-Buffering': 'no',
            },
        )

    # ------------------------------------------------------------------
    # A2A Push-Notification Webhook
    # ------------------------------------------------------------------
    @app.post('/a2a/callback')
    async def specialist_push_notification(request: Request) -> dict[str, str]:
        token = request.headers.get('X-A2A-Notification-Token', '')
        body = await request.json()
        stream_response = ParseDict(body, StreamResponse())
        session = session_registry.get_by_token(token)
        if session is None:
            return {'status': 'unknown_token'}
        # Extract the W3C trace context + HoneyHive session the specialist
        # injected into the callback headers, so relaying chunks + resuming
        # the graph interrupt lands in the same trace + session as the
        # original orchestrator turn.
        with session._callback_trace_context(request):
            await session.handle_push_event(token, stream_response)
        return {'status': 'ok'}

    # ------------------------------------------------------------------
    # Health + meta
    # ------------------------------------------------------------------
    @app.get('/health')
    def healthcheck() -> dict[str, object]:
        return {
            'status': 'ok',
            'openai_base_url': settings.openai_base_url,
            'openai_model': settings.openai_model,
            'sqlite_path': str(settings.sqlite_absolute_path),
            'orchestrator_public_url': settings.orchestrator_public_url,
            'tavily_enabled': settings.tavily_enabled,
            'tavily_configured': settings.tavily_configured,
            'architecture': 'langgraph-stategraph + a2a-push-notifications',
        }

    return app
