"""Specialist executor — turns a config into an A2A agent that supports the
``return_immediately`` + push-notification pattern.

Two output modes (declared on the config, advertised on the agent card):

- **streaming=True** (default): the specialist buffers LLM tokens and pushes
  incremental artifact chunks to the orchestrator webhook as it generates.
  Gives a live activity-trail preview. Roughly O(response_len / buffer) push
  notifications.

- **streaming=False**: the specialist accumulates the full response and pushes
  **one** final artifact on completion (then COMPLETED). Minimal push
  notifications, no live preview. Ideal for cheap/fast specialists or when the
  orchestrator only needs the final answer.

In both modes the orchestrator's webhook correlates the push (via the
``X-A2A-Notification-Token`` header → callback token → interrupt id) and
resumes the paused LangGraph interrupt with the specialist's response.
"""

from __future__ import annotations

import time
from typing import Any

from a2a.helpers import new_task_from_user_message
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events.event_queue import EventQueue
from a2a.server.tasks.task_updater import TaskUpdater
from a2a.types.a2a_pb2 import Message, Part, Role, TaskState
from langchain.agents import create_agent

from nimbus_a2a.config import SpecialistConfig
from nimbus_a2a.tools import build_tavily_research_tool


def _agent_message(*, task_id: str, context_id: str, text: str) -> Message:
    return Message(
        role=Role.ROLE_AGENT,
        task_id=task_id,
        context_id=context_id,
        parts=[Part(text=text)],
    )


class SpecialistExecutor(AgentExecutor):
    """Runs a LangChain agent and streams/returns its output via A2A push.

    The executor is constructed with the chat model + checkpointer (injected by
    the app) and the specialist config. Everything else — task creation,
    ``return_immediately`` handling, push-notification wiring, buffered artifact
    writing — is handled here and by the A2A SDK's ``DefaultRequestHandler``.
    """

    def __init__(
        self,
        config: SpecialistConfig,
        *,
        model: Any,
        checkpointer: Any,
        tavily_api_key: str = '',
        tavily_enabled: bool = False,
    ) -> None:
        self.config = config
        self.model = model
        self.checkpointer = checkpointer
        self.tavily_api_key = tavily_api_key
        self.tavily_enabled = tavily_enabled
        self._agent: Any = None

    # ------------------------------------------------------------------
    # Agent construction
    # ------------------------------------------------------------------

    def _build_tools(self) -> list[Any]:
        tools: list[Any] = list(self.config.extra_tools)
        if self.tavily_enabled and self.tavily_api_key:
            tools.append(
                build_tavily_research_tool(
                    self.tavily_api_key,
                    tool_name=self.config.tavily_tool_name,
                    tool_description=self.config.tavily_tool_description,
                )
            )
        return tools

    def _get_agent(self) -> Any:
        if self._agent is None:
            if self.config.agent_factory is not None:
                self._agent = self.config.agent_factory(
                    self.config, self._build_tools(), self.checkpointer
                )
            else:
                self._agent = create_agent(
                    model=self.model,
                    tools=self._build_tools(),
                    system_prompt=self.config.system_prompt,
                    checkpointer=self.checkpointer,
                    name=f'nimbus-{self.config.table_name_prefix}',
                )
        return self._agent

    # ------------------------------------------------------------------
    # A2A execution
    # ------------------------------------------------------------------

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        task_id = context.task_id
        context_id = context.context_id
        user_input = context.get_user_input().strip()

        if not task_id or not context_id:
            raise ValueError('task_id and context_id are required for specialist execution')

        if context.current_task is None:
            if context.message is None:
                raise ValueError('User message is required to create a task')
            await event_queue.enqueue_event(new_task_from_user_message(context.message))

        label = self.config.label
        updater = TaskUpdater(event_queue, task_id, context_id)
        await updater.start_work(
            _agent_message(
                task_id=task_id,
                context_id=context_id,
                text=f'{label} received the request and is preparing a response.',
            )
        )

        if self.config.streaming:
            await self._run_streaming(context_id, user_input, task_id, updater, label)
        else:
            await self._run_non_streaming(context_id, user_input, task_id, updater, label)

        await updater.complete(
            _agent_message(
                task_id=task_id,
                context_id=context_id,
                text=f'{label} completed the response.',
            )
        )

    async def _run_streaming(
        self,
        context_id: str,
        user_input: str,
        task_id: str,
        updater: TaskUpdater,
        label: str,
    ) -> None:
        """Buffer tokens and flush incremental artifact chunks via push notifications."""
        artifact_id = f'{task_id}-{self.config.table_name_prefix}-response'
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
                name=self.config.artifact_name,
                parts=[Part(text=''.join(buffer))],
                append=not first_chunk,
                last_chunk=False,
                metadata={'source': self.config.table_name_prefix},
            )
            first_chunk = False
            buffer = []
            buffer_len = 0
            last_flush = time.monotonic()

        async for text in self._stream_tokens(context_id, user_input):
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
        label: str,
    ) -> None:
        """Accumulate the full response, then push ONE final artifact."""
        artifact_id = f'{task_id}-{self.config.table_name_prefix}-response'
        parts: list[str] = []
        async for text in self._stream_tokens(context_id, user_input):
            parts.append(text)
        full = ''.join(parts)

        await updater.add_artifact(
            artifact_id=artifact_id,
            name=self.config.artifact_name,
            parts=[Part(text=full)],
            append=False,
            last_chunk=True,
            metadata={'source': self.config.table_name_prefix, 'mode': 'non-streaming'},
        )

    async def _stream_tokens(self, context_id: str, user_input: str):
        """Yield text tokens from the underlying LangChain agent."""
        async for event in self._get_agent().astream_events(
            {
                'messages': [
                    {
                        'role': 'user',
                        'content': user_input,
                    }
                ]
            },
            config={
                'configurable': {'thread_id': f'{context_id}:{self.config.table_name_prefix}'},
                'recursion_limit': 15,
            },
            version='v2',
        ):
            if event.get('event') != 'on_chat_model_stream':
                continue
            chunk = event.get('data', {}).get('chunk')
            text = getattr(chunk, 'text', None)
            if text is None:
                continue
            if isinstance(text, str) and not text:
                continue
            if callable(text):
                text = text()
                if not text:
                    continue
            yield text

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
                text=f'{self.config.name} task cancelled.',
            )
        )
