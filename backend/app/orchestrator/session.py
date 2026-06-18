"""Per-conversation graph sessions and the driver loop.

Each user message creates a :class:`GraphSession` that owns:

- A **driver coroutine** that runs the orchestrator StateGraph via ``astream``.
- An **output queue** (``asyncio.Queue``) of SSE events relayed to the frontend.
- A **resume queue** onto which the webhook pushes ``Command(resume=...)``
  objects when specialists complete.
- A **token → interrupt-id** map correlating A2A push-notification tokens to
  the graph interrupts they should resume.

Driver loop:

1. Run ``graph.astream(input, config, stream_mode=["updates", "custom"])`` and
   relay events to the output queue.
2. When the stream ends, inspect the graph state:
   - If there are pending interrupts → for each *new* interrupt, register a
     callback token and fire an A2A ``SendMessage(return_immediately=True)``
     with a push-notification config pointing at ``/a2a/callback``. Then wait
     on the resume queue.
   - If there are no interrupts → the turn is complete; emit ``done``.
3. Each ``Command(resume=...)`` from the webhook drives another ``astream``
   iteration (resuming one specialist). The loop repeats until the graph
   reaches ``END``.

The webhook handler (see ``service.py``) calls ``session.handle_push_event``
for every push notification. It relays specialist status/chunks to the output
queue and, on a terminal specialist state, pushes a resume command.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from uuid import uuid4

import httpx
from a2a.client import ClientConfig, create_client
from a2a.types.a2a_pb2 import (
    Message,
    Part,
    Role,
    SendMessageConfiguration,
    SendMessageRequest,
    StreamResponse,
    TaskPushNotificationConfig,
    TaskState,
)
from langgraph.types import Command

from app.orchestrator.routing import OrchestratorResponder
from app.settings import Settings

logger = logging.getLogger(__name__)

_TERMINAL_STATES = (
    TaskState.TASK_STATE_COMPLETED,
    TaskState.TASK_STATE_FAILED,
    TaskState.TASK_STATE_CANCELED,
    TaskState.TASK_STATE_REJECTED,
)


class GraphSession:
    """Drives one orchestrator turn (one user message) and streams SSE events."""

    def __init__(
        self,
        *,
        graph: Any,
        config: dict[str, Any],
        settings: Settings,
        responder: OrchestratorResponder,
        user_input: str,
        context_id: str,
        registry: 'SessionRegistry',
    ) -> None:
        self.graph = graph
        self.config = config
        self.settings = settings
        self.responder = responder
        self.user_input = user_input
        self.context_id = context_id
        self.registry = registry

        self.output_queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        self.resume_queue: asyncio.Queue[Command] = asyncio.Queue()

        # callback_token -> interrupt_id
        self.token_to_interrupt: dict[str, str] = {}
        # interrupt_id -> interrupt payload (specialist_name, specialist_url, query, context_id)
        self.interrupt_meta: dict[str, dict[str, Any]] = {}
        # interrupt_ids for which we have already dispatched the A2A request
        self.dispatched: set[str] = set()
        # interrupt_id -> accumulated specialist response chunks
        self.specialist_chunks: dict[str, list[str]] = {}
        # RouteDecision dict (populated from graph state after route_node)
        self.route: dict[str, Any] | None = None

        self._driver_task: asyncio.Task | None = None

    # ------------------------------------------------------------------
    # Driver
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Launch the driver coroutine in the background."""
        self._driver_task = asyncio.create_task(self._drive())

    async def _drive(self) -> None:
        try:
            input_: Any = {
                'user_input': self.user_input,
                'context_id': self.context_id,
                'route': None,
                'specialist_responses': [],
                'final_response': '',
            }
            while True:
                async for mode, chunk in self.graph.astream(
                    input_,
                    self.config,
                    stream_mode=['updates', 'custom'],
                ):
                    self._handle_stream_chunk(mode, chunk)

                snap = await self.graph.aget_state(self.config)
                pending = [
                    (t, intr)
                    for t in snap.tasks
                    for intr in t.interrupts
                ]

                # Refresh route from state (available after route_node ran).
                if snap.values and snap.values.get('route'):
                    self.route = snap.values['route']

                if not pending:
                    # No interrupts → turn complete.
                    break

                # Dispatch A2A requests for any newly-paused interrupts.
                await self._dispatch_new_interrupts(pending)

                # Wait for the webhook to push a resume command.
                input_ = await self.resume_queue.get()

            await self._finish()
        except Exception as exc:  # noqa: BLE001
            logger.exception('Graph driver failed for context %s', self.context_id)
            await self.output_queue.put(
                {'type': 'error', 'text': f'Orchestrator error: {exc}'}
            )
            await self.output_queue.put(None)
            return
        await self.output_queue.put(None)

    def _handle_stream_chunk(self, mode: str, chunk: Any) -> None:
        if mode == 'custom':
            # Custom events from get_stream_writer(): status / token / etc.
            if isinstance(chunk, dict):
                self.output_queue.put_nowait(chunk)
        elif mode == 'updates':
            # Node output diffs. We mainly use these to capture the route early.
            if isinstance(chunk, dict):
                for _node, update in chunk.items():
                    if isinstance(update, dict) and update.get('route'):
                        self.route = update['route']

    async def _dispatch_new_interrupts(self, pending: list[tuple[Any, Any]]) -> None:
        for _task, intr in pending:
            if intr.id in self.dispatched:
                continue
            meta = dict(intr.value or {})
            self.interrupt_meta[intr.id] = meta
            self.dispatched.add(intr.id)

            callback_token = f'nimbus-{uuid4()}'
            self.token_to_interrupt[callback_token] = intr.id
            self.registry.register(callback_token, self)

            name = meta.get('specialist_name', 'specialist')
            self.output_queue.put_nowait(
                {
                    'type': 'status',
                    'phase': 'specialist_working',
                    'text': f'{name} is working…',
                    'specialist_name': name,
                }
            )
            # Fire the A2A request (return_immediately) in the background.
            asyncio.create_task(
                self._send_specialist_request(meta, callback_token)
            )

    async def _send_specialist_request(
        self, meta: dict[str, Any], callback_token: str
    ) -> None:
        """Send A2A SendMessage with return_immediately + push config."""
        callback_url = f'{self.settings.orchestrator_internal_url}/a2a/callback'
        try:
            client = await create_client(
                meta['specialist_url'],
                client_config=ClientConfig(
                    streaming=False,
                    httpx_client=httpx.AsyncClient(timeout=300.0),
                ),
            )
            request = SendMessageRequest(
                message=Message(
                    message_id=str(uuid4()),
                    role=Role.ROLE_USER,
                    context_id=meta.get('context_id', self.context_id),
                    parts=[Part(text=meta['query'])],
                ),
                configuration=SendMessageConfiguration(
                    return_immediately=True,
                    task_push_notification_config=TaskPushNotificationConfig(
                        url=callback_url,
                        token=callback_token,
                    ),
                ),
            )
            async for _event in client.send_message(request):
                # The initial Task response — specialist continues in background.
                pass
        except Exception:  # noqa: BLE001
            logger.exception(
                'Failed to dispatch A2A request to %s', meta.get('specialist_url')
            )
            # Resume the interrupt with an error so the graph isn't stuck.
            interrupt_id = self.token_to_interrupt.get(callback_token)
            if interrupt_id is not None:
                await self.resume_queue.put(
                    Command(resume={interrupt_id: '[specialist unavailable]'})
                )

    async def _finish(self) -> None:
        """Record the exchange for conversation continuity, then emit done."""
        final_response = ''
        try:
            snap = await self.graph.aget_state(self.config)
            final_response = (snap.values or {}).get('final_response', '') or ''
        except Exception:  # noqa: BLE001
            pass

        # Inject the user + assistant exchange into the responder's thread so
        # future direct (non-routed) responses have full context.
        if final_response and self.route is not None:
            try:
                await self.responder.record_exchange(
                    user_input=self.user_input,
                    assistant_response=final_response,
                    thread_id=self.context_id,
                )
            except Exception:  # noqa: BLE001
                logger.warning('record_exchange failed', exc_info=True)

        await self.output_queue.put(
            {'type': 'done', 'final_response': final_response}
        )
        # Clean up webhook token registrations.
        for token in list(self.token_to_interrupt.keys()):
            self.registry.forget(token)

    # ------------------------------------------------------------------
    # Webhook → session
    # ------------------------------------------------------------------

    async def handle_push_event(
        self, token: str, stream_response: StreamResponse
    ) -> None:
        """Called by the webhook for each push notification from a specialist."""
        interrupt_id = self.token_to_interrupt.get(token)
        if interrupt_id is None:
            return
        meta = self.interrupt_meta.get(interrupt_id, {})
        name = meta.get('specialist_name', 'specialist')

        if stream_response.HasField('status_update'):
            status = stream_response.status_update.status
            state = status.state
            if state == TaskState.TASK_STATE_WORKING:
                self.output_queue.put_nowait(
                    {
                        'type': 'status',
                        'phase': 'specialist_working',
                        'text': f'{name} is working…',
                        'specialist_name': name,
                    }
                )
            if state in _TERMINAL_STATES:
                await self._resume_specialist(interrupt_id, name)

        elif stream_response.HasField('artifact_update'):
            for part in stream_response.artifact_update.artifact.parts:
                if part.HasField('text'):
                    await self._accumulate_chunk(interrupt_id, name, part.text)

        elif stream_response.HasField('message'):
            for part in stream_response.message.parts:
                if part.HasField('text'):
                    await self._accumulate_chunk(interrupt_id, name, part.text)
            await self._resume_specialist(interrupt_id, name)

        elif stream_response.HasField('task'):
            tstate = stream_response.task.status.state
            if tstate in _TERMINAL_STATES:
                await self._resume_specialist(interrupt_id, name)

    async def _accumulate_chunk(
        self, interrupt_id: str, name: str, text: str
    ) -> None:
        self.specialist_chunks.setdefault(interrupt_id, []).append(text)
        # Specialist artifact chunks are relayed to the activity trail (live,
        # collapsible) — showcasing the push-notification delivery. The main
        # response is always produced by a graph node (respond / synthesize /
        # assemble) so the graph remains the single source of truth.
        self.output_queue.put_nowait(
            {
                'type': 'specialist_chunk',
                'specialist_name': name,
                'text': text,
            }
        )

    async def _resume_specialist(self, interrupt_id: str, name: str) -> None:
        # Guard against double-resume (a late duplicate push notification).
        if interrupt_id not in self.interrupt_meta:
            return
        response = ''.join(self.specialist_chunks.get(interrupt_id, []))
        # Clean up so a late duplicate push won't resume twice.
        self.interrupt_meta.pop(interrupt_id, None)

        self.output_queue.put_nowait(
            {
                'type': 'status',
                'phase': 'specialist_done',
                'text': f'{name} completed its response.',
                'specialist_name': name,
            }
        )
        await self.resume_queue.put(Command(resume={interrupt_id: response}))


class SessionRegistry:
    """Maps callback tokens → active GraphSessions (for webhook dispatch)."""

    def __init__(self) -> None:
        self._by_token: dict[str, GraphSession] = {}

    def register(self, token: str, session: GraphSession) -> None:
        self._by_token[token] = session

    def get_by_token(self, token: str) -> GraphSession | None:
        return self._by_token.get(token)

    def forget(self, token: str) -> None:
        self._by_token.pop(token, None)


# Module-level singleton — shared between the SSE endpoint and the webhook.
session_registry = SessionRegistry()
