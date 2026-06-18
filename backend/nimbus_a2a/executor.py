"""Specialist executor — the core abstraction teams subclass.

A specialist team builds their agent with **any** framework (LangChain,
LangGraph, Pydantic AI, DSPy, raw LLM calls, …) and subclasses
:class:`SpecialistExecutor`, overriding **one** of:

- :meth:`stream` — an async generator yielding text chunks (use when
  ``streaming=True``; gives a live activity-trail preview).
- :meth:`invoke` — returns the full response string (use when
  ``streaming=False``; pushes a single final artifact).

If only one is overridden, the other derives from it automatically
(``invoke`` collects ``stream``; ``stream`` yields ``invoke`` as one chunk).

The SDK handles everything else: the A2A protocol, task lifecycle, and the
``return_immediately`` + push-notification artifact chunking. With
``streaming=True`` it buffers tokens and pushes incremental artifact chunks;
with ``streaming=False`` it accumulates the response and pushes one final
artifact. Teams never touch A2A internals.
"""

from __future__ import annotations

import time
from typing import AsyncIterator

from a2a.helpers import new_task_from_user_message
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events.event_queue import EventQueue
from a2a.server.tasks.task_updater import TaskUpdater
from a2a.types.a2a_pb2 import Message, Part, Role


def _agent_message(*, task_id: str, context_id: str, text: str) -> Message:
    return Message(
        role=Role.ROLE_AGENT,
        task_id=task_id,
        context_id=context_id,
        parts=[Part(text=text)],
    )


class SpecialistExecutor(AgentExecutor):
    """Base class for A2A specialists. Override ``invoke`` or ``stream``.

    Args:
        streaming: Declares the specialist's output mode.
            ``True`` → the SDK pushes buffered incremental artifact chunks via
            push notifications (live preview). ``False`` → the SDK accumulates
            the full response and pushes a single final artifact (minimal
            notifications). Advertised on the agent card's ``streaming``
            capability by :func:`nimbus_a2a.create_specialist_app`.
        artifact_name: Name label on the artifact pushed to the orchestrator.
        label: Human-friendly name used in the start/complete status messages.
    """

    def __init__(
        self,
        *,
        streaming: bool = True,
        artifact_name: str = 'specialist-response',
        label: str = 'Specialist',
    ) -> None:
        self.streaming = streaming
        self.artifact_name = artifact_name
        self.label = label

    # ------------------------------------------------------------------
    # Lifecycle hooks (override if you need async resource setup/teardown)
    # ------------------------------------------------------------------

    async def startup(self) -> None:
        """Called once inside the app lifespan, before any request.

        Override to build async resources (DB connections, checkpointers,
        model clients…). The default is a no-op.
        """

    async def shutdown(self) -> None:
        """Called once on app shutdown. Override to close resources."""

    # ------------------------------------------------------------------
    # Team-implemented agent logic (override ONE of these)
    # ------------------------------------------------------------------

    async def invoke(self, message: str, *, context_id: str) -> str:
        """Return the full response string.

        Override this for non-streaming agents (``streaming=False``). The
        default implementation collects :meth:`stream` into a string, so if you
        override :meth:`stream` you get this for free.
        """
        chunks: list[str] = []
        async for chunk in self.stream(message, context_id=context_id):
            chunks.append(chunk)
        return ''.join(chunks)

    async def stream(self, message: str, *, context_id: str) -> AsyncIterator[str]:
        """Yield text chunks.

        Override this for streaming agents (``streaming=True``). The default
        implementation yields :meth:`invoke` as a single chunk, so if you
        override :meth:`invoke` you get this for free.
        """
        yield await self.invoke(message, context_id=context_id)

    # ------------------------------------------------------------------
    # A2A AgentExecutor — handled by the SDK (do not override)
    # ------------------------------------------------------------------

    def _ensure_implemented(self) -> None:
        # Detect the "neither overridden" case to give a clear error instead
        # of infinite mutual recursion between invoke() and stream().
        invoked_overridden = type(self).invoke is not SpecialistExecutor.invoke
        streamed_overridden = type(self).stream is not SpecialistExecutor.stream
        if not (invoked_overridden or streamed_overridden):
            raise NotImplementedError(
                f'{type(self).__name__} must override invoke() or stream()'
            )

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        self._ensure_implemented()

        task_id = context.task_id
        context_id = context.context_id
        if not task_id or not context_id:
            raise ValueError('task_id and context_id are required for specialist execution')

        user_input = context.get_user_input().strip()
        if context.current_task is None:
            if context.message is None:
                raise ValueError('User message is required to create a task')
            await event_queue.enqueue_event(new_task_from_user_message(context.message))

        updater = TaskUpdater(event_queue, task_id, context_id)
        await updater.start_work(
            _agent_message(
                task_id=task_id,
                context_id=context_id,
                text=f'{self.label} received the request and is preparing a response.',
            )
        )

        if self.streaming:
            await self._run_streaming(context_id, user_input, task_id, updater)
        else:
            await self._run_non_streaming(context_id, user_input, task_id, updater)

        await updater.complete(
            _agent_message(
                task_id=task_id,
                context_id=context_id,
                text=f'{self.label} completed the response.',
            )
        )

    async def _run_streaming(
        self,
        context_id: str,
        user_input: str,
        task_id: str,
        updater: TaskUpdater,
    ) -> None:
        """Buffer tokens from :meth:`stream` and push incremental artifacts."""
        artifact_id = f'{task_id}-response'
        FLUSH_CHARS = 2000
        FLUSH_INTERVAL = 2.0
        buffer: list[str] = []
        buffer_len = 0
        last_flush = time.monotonic()
        first_chunk = True

        async def flush(force: bool = False) -> None:
            nonlocal first_chunk, buffer, buffer_len, last_flush
            if not buffer:
                return
            if not force and buffer_len < FLUSH_CHARS and (time.monotonic() - last_flush) < FLUSH_INTERVAL:
                return
            await updater.add_artifact(
                artifact_id=artifact_id,
                name=self.artifact_name,
                parts=[Part(text=''.join(buffer))],
                append=not first_chunk,
                last_chunk=False,
            )
            first_chunk = False
            buffer = []
            buffer_len = 0
            last_flush = time.monotonic()

        async for text in self.stream(user_input, context_id=context_id):
            if not text:
                continue
            buffer.append(text)
            buffer_len += len(text)
            await flush()

        await flush(force=True)

    async def _run_non_streaming(
        self,
        context_id: str,
        user_input: str,
        task_id: str,
        updater: TaskUpdater,
    ) -> None:
        """Accumulate :meth:`invoke` and push ONE final artifact."""
        artifact_id = f'{task_id}-response'
        full = await self.invoke(user_input, context_id=context_id)
        await updater.add_artifact(
            artifact_id=artifact_id,
            name=self.artifact_name,
            parts=[Part(text=full)],
            append=False,
            last_chunk=True,
        )

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        task_id = context.task_id
        context_id = context.context_id
        if not task_id or not context_id:
            return
        updater = TaskUpdater(event_queue, task_id, context_id)
        await updater.cancel(
            _agent_message(
                task_id=task_id,
                context_id=context_id,
                text=f'{self.label} task cancelled.',
            )
        )
