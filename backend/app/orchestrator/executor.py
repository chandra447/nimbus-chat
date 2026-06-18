from __future__ import annotations

import asyncio
from uuid import uuid4

import httpx
from a2a.client import create_client, ClientConfig
from a2a.helpers import new_task_from_user_message
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events.event_queue import EventQueue
from a2a.server.tasks.task_updater import TaskUpdater
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
from langchain_core.messages import AIMessage, HumanMessage

from app.orchestrator.callback import callback_manager
from app.orchestrator.registry import SpecialistRegistry
from app.orchestrator.routing import (
    OrchestratorResponder,
    OrchestratorRouter,
    OrchestratorSynthesizer,
)
from app.settings import Settings


def agent_message(
    *,
    task_id: str,
    context_id: str,
    text: str,
) -> Message:
    return Message(
        role=Role.ROLE_AGENT,
        task_id=task_id,
        context_id=context_id,
        parts=[Part(text=text)],
    )


class OrchestratorExecutor(AgentExecutor):
    def __init__(
        self,
        settings: Settings,
        registry: SpecialistRegistry,
        router: OrchestratorRouter,
        responder: OrchestratorResponder,
        synthesizer: OrchestratorSynthesizer,
    ) -> None:
        self.settings = settings
        self.registry = registry
        self.router = router
        self.responder = responder
        self.synthesizer = synthesizer

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        task_id = context.task_id
        context_id = context.context_id
        user_input = context.get_user_input().strip()

        if not task_id or not context_id:
            raise ValueError('task_id and context_id are required for orchestration')

        if context.current_task is None:
            if context.message is None:
                raise ValueError('User message is required to create a task')
            await event_queue.enqueue_event(
                new_task_from_user_message(context.message)
            )

        updater = TaskUpdater(event_queue, task_id, context_id)
        await updater.start_work(
            agent_message(
                task_id=task_id,
                context_id=context_id,
                text='Orchestrator received the request and is analyzing routing options.',
            )
        )

        decision = await self.router.decide(user_input, thread_id=context_id)

        if decision.should_route and decision.specialists:
            names = [s.name for s in decision.specialists]
            if len(decision.specialists) == 1:
                specialist = decision.specialists[0]
                await updater.update_status(
                    TaskState.TASK_STATE_WORKING,
                    message=agent_message(
                        task_id=task_id,
                        context_id=context_id,
                        text=(
                            f"Routing to specialist '{specialist.name}'. "
                            f"Reason: {specialist.rationale or decision.rationale or 'specialist matched request.'}"
                        ),
                    ),
                )
                await self._stream_specialist_response(
                    specialist_url=specialist.url,
                    specialist_name=specialist.name,
                    user_input=user_input,
                    task_id=task_id,
                    context_id=context_id,
                    updater=updater,
                )
            else:
                # Parallel fan-out to multiple specialists + synthesis.
                await updater.update_status(
                    TaskState.TASK_STATE_WORKING,
                    message=agent_message(
                        task_id=task_id,
                        context_id=context_id,
                        text=(
                            f"Routing to {len(names)} specialists in parallel: "
                            f"{', '.join(names)}. Reason: {decision.rationale or 'multiple domains matched.'}"
                        ),
                    ),
                )
                await self._stream_parallel_specialist_response(
                    specialists=decision.specialists,
                    needs_synthesis=decision.needs_synthesis,
                    user_input=user_input,
                    task_id=task_id,
                    context_id=context_id,
                    updater=updater,
                )
            return

        await updater.update_status(
            TaskState.TASK_STATE_WORKING,
            message=agent_message(
                task_id=task_id,
                context_id=context_id,
                text='No specialist matched clearly. Responding directly from the orchestrator.',
            ),
        )
        await self._stream_direct_response(
            user_input=user_input,
            task_id=task_id,
            context_id=context_id,
            updater=updater,
        )

    async def _stream_direct_response(
        self,
        *,
        user_input: str,
        task_id: str,
        context_id: str,
        updater: TaskUpdater,
    ) -> None:
        artifact_id = f'{task_id}-direct-response'
        first_chunk = True
        async for token in self.responder.stream_text(user_input, thread_id=context_id):
            await updater.add_artifact(
                artifact_id=artifact_id,
                name='orchestrator-response',
                parts=[Part(text=token)],
                append=not first_chunk,
                last_chunk=False,
            )
            first_chunk = False

        await updater.complete(
            agent_message(
                task_id=task_id,
                context_id=context_id,
                text='Orchestrator completed the response.',
            )
        )

    async def _call_single_specialist(
        self,
        *,
        specialist_name: str,
        specialist_url: str,
        user_input: str,
        task_id: str,
        context_id: str,
        updater: TaskUpdater,
    ) -> tuple[str, str]:
        """Call one specialist, emit status updates, return (name, full_response).

        When ``async_specialist_mode`` is enabled, uses ``return_immediately=True``
        with push notifications — the specialist processes in the background and
        POSTs events to the orchestrator's webhook. No long-held A2A connection.
        """
        if self.settings.async_specialist_mode:
            return await self._call_specialist_async(
                specialist_name=specialist_name,
                specialist_url=specialist_url,
                user_input=user_input,
                task_id=task_id,
                context_id=context_id,
                updater=updater,
            )
        return await self._call_specialist_streaming(
                specialist_name=specialist_name,
                specialist_url=specialist_url,
                user_input=user_input,
                task_id=task_id,
                context_id=context_id,
                updater=updater,
            )

    async def _call_specialist_async(
        self,
        *,
        specialist_name: str,
        specialist_url: str,
        user_input: str,
        task_id: str,
        context_id: str,
        updater: TaskUpdater,
    ) -> tuple[str, str]:
        """Call a specialist with return_immediately + push notifications.

        1. Register a callback queue keyed by a unique token.
        2. Send ``SendMessage`` (non-streaming) with ``return_immediately=True``
           and a push-notification config pointing to the orchestrator webhook.
        3. The specialist returns immediately with the initial Task.
        4. The specialist processes in the background and POSTs events
           (status updates, artifact chunks) to ``/a2a/callback``.
        5. We consume events from the callback queue and relay them.
        """
        callback_token = f'nimbus-{uuid4()}'
        callback_url = f'{self.settings.orchestrator_internal_url}/a2a/callback'
        queue = callback_manager.create_queue(callback_token)

        try:
            # Non-streaming client — uses JSON-RPC ``SendMessage`` (not SSE).
            specialist_config = ClientConfig(
                streaming=False,
                httpx_client=httpx.AsyncClient(timeout=300.0),
            )
            client = await create_client(
                specialist_url, client_config=specialist_config
            )

            request = SendMessageRequest(
                message=Message(
                    message_id=str(uuid4()),
                    role=Role.ROLE_USER,
                    context_id=context_id,
                    parts=[Part(text=user_input)],
                ),
                configuration=SendMessageConfiguration(
                    return_immediately=True,
                    task_push_notification_config=TaskPushNotificationConfig(
                        url=callback_url,
                        token=callback_token,
                    ),
                ),
            )

            # send_message with streaming=False yields one StreamResponse
            # (the initial Task) and returns. The specialist continues
            # in the background.
            async for event in client.send_message(request):
                if event.HasField('task'):
                    state_name = TaskState.Name(event.task.status.state)
                    await updater.update_status(
                        TaskState.TASK_STATE_WORKING,
                        message=agent_message(
                            task_id=task_id,
                            context_id=context_id,
                            text=f'{specialist_name} received the request ({state_name.lower()}).',
                        ),
                        metadata={'specialist_name': specialist_name, 'async': True},
                    )

            # Now consume push-notification events from the webhook queue.
            response_chunks: list[str] = []
            while True:
                try:
                    push_event: StreamResponse = await asyncio.wait_for(
                        queue.get(), timeout=300.0
                    )
                except asyncio.TimeoutError:
                    await updater.update_status(
                        TaskState.TASK_STATE_WORKING,
                        message=agent_message(
                            task_id=task_id,
                            context_id=context_id,
                            text=f'{specialist_name} timed out.',
                        ),
                    )
                    break

                if push_event.HasField('status_update'):
                    status = push_event.status_update.status
                    state = status.state
                    state_name = TaskState.Name(state)
                    msg_text = ''
                    if status.message and status.message.parts:
                        for p in status.message.parts:
                            if p.HasField('text'):
                                msg_text = p.text
                    await updater.update_status(
                        TaskState.TASK_STATE_WORKING,
                        message=agent_message(
                            task_id=task_id,
                            context_id=context_id,
                            text=f'{specialist_name} is working… ({state_name.lower()})',
                        ),
                        metadata={'specialist_name': specialist_name, 'specialist_state': state_name},
                    )
                    # Terminal state — specialist is done.
                    if state in (
                        TaskState.TASK_STATE_COMPLETED,
                        TaskState.TASK_STATE_FAILED,
                        TaskState.TASK_STATE_CANCELED,
                        TaskState.TASK_STATE_REJECTED,
                    ):
                        break

                elif push_event.HasField('artifact_update'):
                    for part in push_event.artifact_update.artifact.parts:
                        if part.HasField('text'):
                            response_chunks.append(part.text)

                elif push_event.HasField('message'):
                    for part in push_event.message.parts:
                        if part.HasField('text'):
                            response_chunks.append(part.text)
                    break  # Message is terminal.

                elif push_event.HasField('task'):
                    state = push_event.task.status.state
                    if state in (
                        TaskState.TASK_STATE_COMPLETED,
                        TaskState.TASK_STATE_FAILED,
                        TaskState.TASK_STATE_CANCELED,
                        TaskState.TASK_STATE_REJECTED,
                    ):
                        break

            await updater.update_status(
                TaskState.TASK_STATE_WORKING,
                message=agent_message(
                    task_id=task_id,
                    context_id=context_id,
                    text=f'{specialist_name} completed its response.',
                ),
                metadata={'specialist_name': specialist_name, 'specialist_done': True},
            )
            return specialist_name, ''.join(response_chunks)
        finally:
            callback_manager.remove_queue(callback_token)

    async def _call_specialist_streaming(
        self,
        *,
        specialist_name: str,
        specialist_url: str,
        user_input: str,
        task_id: str,
        context_id: str,
        updater: TaskUpdater,
    ) -> tuple[str, str]:
        """Call one specialist via streaming SSE (legacy synchronous mode)."""
        specialist_config = ClientConfig(
            httpx_client=httpx.AsyncClient(timeout=300.0)
        )
        client = await create_client(specialist_url, client_config=specialist_config)
        request = SendMessageRequest(
            message=Message(
                message_id=str(uuid4()),
                role=Role.ROLE_USER,
                context_id=context_id,
                parts=[Part(text=user_input)],
            )
        )

        response_chunks: list[str] = []
        async for event in client.send_message(request):
            if event.HasField('status_update'):
                state_name = TaskState.Name(event.status_update.status.state)
                await updater.update_status(
                    TaskState.TASK_STATE_WORKING,
                    message=agent_message(
                        task_id=task_id,
                        context_id=context_id,
                        text=f'{specialist_name} is working… ({state_name.lower()})',
                    ),
                    metadata={'specialist_state': state_name, 'specialist_name': specialist_name},
                )
            elif event.HasField('artifact_update'):
                for part in event.artifact_update.artifact.parts:
                    if part.HasField('text'):
                        response_chunks.append(part.text)
            elif event.HasField('message'):
                for part in event.message.parts:
                    if part.HasField('text'):
                        response_chunks.append(part.text)

        await updater.update_status(
            TaskState.TASK_STATE_WORKING,
            message=agent_message(
                task_id=task_id,
                context_id=context_id,
                text=f'{specialist_name} completed its response.',
            ),
            metadata={'specialist_name': specialist_name, 'specialist_done': True},
        )
        return specialist_name, ''.join(response_chunks)

    async def _stream_specialist_response(
        self,
        *,
        specialist_url: str,
        specialist_name: str,
        user_input: str,
        task_id: str,
        context_id: str,
        updater: TaskUpdater,
    ) -> None:
        """Stream a single specialist's response directly to the client."""
        specialist_config = ClientConfig(
            httpx_client=httpx.AsyncClient(timeout=300.0)
        )
        client = await create_client(specialist_url, client_config=specialist_config)
        request = SendMessageRequest(
            message=Message(
                message_id=str(uuid4()),
                role=Role.ROLE_USER,
                context_id=context_id,
                parts=[Part(text=user_input)],
            )
        )

        artifact_id = f'{task_id}-specialist-response'
        artifact_created = False
        specialist_response_text: list[str] = []
        async for event in client.send_message(request):
            if event.HasField('status_update'):
                state_name = TaskState.Name(event.status_update.status.state)
                await updater.update_status(
                    TaskState.TASK_STATE_WORKING,
                    message=agent_message(
                        task_id=task_id,
                        context_id=context_id,
                        text=f'{specialist_name} is working… ({state_name.lower()})',
                    ),
                    metadata={'specialist_state': state_name, 'specialist_name': specialist_name},
                )
            elif event.HasField('artifact_update'):
                artifact = event.artifact_update.artifact
                for part in artifact.parts:
                    if part.HasField('text'):
                        specialist_response_text.append(part.text)
                await updater.add_artifact(
                    artifact_id=artifact_id,
                    name=artifact.name or 'specialist-response',
                    parts=list(artifact.parts),
                    append=artifact_created,
                    last_chunk=event.artifact_update.last_chunk,
                    metadata={'source': 'specialist'},
                )
                artifact_created = True
            elif event.HasField('message'):
                for part in event.message.parts:
                    if part.HasField('text'):
                        specialist_response_text.append(part.text)
                await updater.add_artifact(
                    artifact_id=artifact_id,
                    name='specialist-response',
                    parts=list(event.message.parts),
                    append=False,
                    last_chunk=True,
                    metadata={'source': 'specialist-message'},
                )

        full_specialist_response = ''.join(specialist_response_text)
        if full_specialist_response:
            await self.responder.record_exchange(
                user_input=user_input,
                assistant_response=full_specialist_response,
                thread_id=context_id,
            )

        await updater.complete(
            agent_message(
                task_id=task_id,
                context_id=context_id,
                text=f'{specialist_name} completed the response and streamed it back.',
            )
        )

    async def _stream_parallel_specialist_response(
        self,
        *,
        specialists: list,
        needs_synthesis: bool,
        user_input: str,
        task_id: str,
        context_id: str,
        updater: TaskUpdater,
    ) -> None:
        """Fan out to multiple specialists in parallel.

        When ``needs_synthesis`` is True: collect all responses, run the
        synthesizer LLM, and stream a unified answer (extra LLM call).

        When ``needs_synthesis`` is False: stream each specialist's response
        directly to the client with a section header — no extra LLM call.
        Responses are emitted as each specialist completes (not waiting for
        all to finish) via ``asyncio.as_completed``.
        """
        if needs_synthesis:
            await self._fan_out_with_synthesis(
                specialists=specialists,
                user_input=user_input,
                task_id=task_id,
                context_id=context_id,
                updater=updater,
            )
        else:
            await self._fan_out_no_synthesis(
                specialists=specialists,
                user_input=user_input,
                task_id=task_id,
                context_id=context_id,
                updater=updater,
            )

    async def _fan_out_with_synthesis(
        self,
        *,
        specialists: list,
        user_input: str,
        task_id: str,
        context_id: str,
        updater: TaskUpdater,
    ) -> None:
        """Collect all specialist responses, synthesize, stream unified answer."""
        tasks = [
            self._call_single_specialist(
                specialist_name=s.name,
                specialist_url=s.url,
                user_input=user_input,
                task_id=task_id,
                context_id=context_id,
                updater=updater,
            )
            for s in specialists
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        specialist_responses: list[tuple[str, str]] = []
        for i, result in enumerate(results):
            name = specialists[i].name
            if isinstance(result, Exception):
                await updater.update_status(
                    TaskState.TASK_STATE_WORKING,
                    message=agent_message(
                        task_id=task_id,
                        context_id=context_id,
                        text=f'{name} failed: {result}',
                    ),
                )
            else:
                specialist_responses.append(result)

        if not specialist_responses:
            await updater.complete(
                agent_message(
                    task_id=task_id,
                    context_id=context_id,
                    text='All specialists failed. Unable to produce a response.',
                )
            )
            return

        names = [name for name, _ in specialist_responses]
        await updater.update_status(
            TaskState.TASK_STATE_WORKING,
            message=agent_message(
                task_id=task_id,
                context_id=context_id,
                text=f'All specialists completed ({", ".join(names)}). Synthesizing a unified response…',
            ),
            metadata={'phase': 'synthesizing'},
        )

        artifact_id = f'{task_id}-synthesized-response'
        first_chunk = True
        synthesized_text: list[str] = []
        async for token in self.synthesizer.stream_synthesis(
            user_input,
            specialist_responses,
            thread_id=context_id,
        ):
            synthesized_text.append(token)
            await updater.add_artifact(
                artifact_id=artifact_id,
                name='synthesized-response',
                parts=[Part(text=token)],
                append=not first_chunk,
                last_chunk=False,
                metadata={'source': 'synthesizer'},
            )
            first_chunk = False

        full_synthesized_response = ''.join(synthesized_text)
        if full_synthesized_response:
            await self.responder.record_exchange(
                user_input=user_input,
                assistant_response=full_synthesized_response,
                thread_id=context_id,
            )

        await updater.complete(
            agent_message(
                task_id=task_id,
                context_id=context_id,
                text=f'Synthesized response from {", ".join(names)} completed.',
            )
        )

    async def _fan_out_no_synthesis(
        self,
        *,
        specialists: list,
        user_input: str,
        task_id: str,
        context_id: str,
        updater: TaskUpdater,
    ) -> None:
        """Stream each specialist's response directly with section headers.

        No extra LLM call — saves tokens. Responses are emitted as each
        specialist completes (via ``asyncio.as_completed``).
        """
        artifact_id = f'{task_id}-parallel-response'
        artifact_created = False
        all_responses: list[str] = []

        # Build coroutines keyed by specialist name for status messages.
        coros = {
            s.name: self._call_single_specialist(
                specialist_name=s.name,
                specialist_url=s.url,
                user_input=user_input,
                task_id=task_id,
                context_id=context_id,
                updater=updater,
            )
            for s in specialists
        }

        # Process specialists as they complete.
        pending = {asyncio.create_task(coro, name=name): name
                  for name, coro in coros.items()}

        while pending:
            done, pending = await asyncio.wait(
                pending, return_when=asyncio.FIRST_COMPLETED
            )
            for task in done:
                name = task.get_name()
                try:
                    specialist_name, response = await task
                except Exception as exc:
                    await updater.update_status(
                        TaskState.TASK_STATE_WORKING,
                        message=agent_message(
                            task_id=task_id,
                            context_id=context_id,
                            text=f'{name} failed: {exc}',
                        ),
                    )
                    continue

                # Emit a section header + the specialist's full response.
                header = f'## {specialist_name}\n\n'
                chunk = header + response + '\n\n---\n\n'
                all_responses.append(chunk)

                await updater.add_artifact(
                    artifact_id=artifact_id,
                    name='parallel-response',
                    parts=[Part(text=chunk)],
                    append=artifact_created,
                    last_chunk=False,
                    metadata={'source': 'specialist', 'specialist_name': specialist_name},
                )
                artifact_created = True

        if all_responses:
            combined = ''.join(all_responses)
            await self.responder.record_exchange(
                user_input=user_input,
                assistant_response=combined,
                thread_id=context_id,
            )

        await updater.complete(
            agent_message(
                task_id=task_id,
                context_id=context_id,
                text=f'Responses from {len(specialists)} specialists completed.',
            )
        )

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        task_id = context.task_id
        context_id = context.context_id
        if not task_id or not context_id:
            return

        updater = TaskUpdater(event_queue, task_id, context_id)
        await updater.cancel(
            agent_message(
                task_id=task_id,
                context_id=context_id,
                text='Task cancelled by client.',
            )
        )
