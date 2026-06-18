"""Generic specialist builder — shared by travel, nutrition, and future specialists."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from a2a.helpers import new_task_from_user_message
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events.event_queue import EventQueue
from a2a.server.tasks.task_updater import TaskUpdater
from a2a.types import AgentCard
from a2a.types.a2a_pb2 import (
    AgentCapabilities,
    AgentInterface,
    AgentProvider,
    AgentSkill,
    Message,
    Part,
    Role,
    TaskState,
)
from langchain.agents import create_agent
from langchain_core.tools import StructuredTool
from tavily import TavilyClient

from app.llm import build_chat_model
from app.settings import Settings


# ---------------------------------------------------------------------------
# Tavily research tool (generic)
# ---------------------------------------------------------------------------


def _tavily_search(settings: Settings, query: str) -> str:
    if not settings.tavily_configured:
        return 'Web research is not configured (Tavily API key missing).'
    client = TavilyClient(api_key=settings.tavily_api_key)
    response = client.search(
        query=query,
        topic='general',
        max_results=5,
        search_depth='advanced',
        include_answer=True,
    )
    answer = response.get('answer', '')
    results = response.get('results', []) or []

    lines = []
    if answer:
        lines.append(f'Summary: {answer}')
    if results:
        lines.append('Relevant sources:')
    for item in results[:5]:
        title = item.get('title', 'Untitled')
        url = item.get('url', '')
        content = (item.get('content') or '').strip().replace('\n', ' ')
        snippet = content[:280]
        lines.append(f'- {title} ({url}) :: {snippet}')
    return '\n'.join(lines)


def build_tavily_research_tool(
    settings: Settings,
    *,
    tool_name: str = 'research_web',
    tool_description: str = 'Search the web for up-to-date information.',
) -> StructuredTool:
    """Build a LangChain tool that wraps Tavily search."""

    def _search(query: str) -> str:
        return _tavily_search(settings, query)

    return StructuredTool.from_function(
        func=_search,
        name=tool_name,
        description=tool_description,
    )


# ---------------------------------------------------------------------------
# Specialist configuration
# ---------------------------------------------------------------------------


@dataclass
class SpecialistSkillSpec:
    id: str
    name: str
    description: str
    tags: list[str]
    examples: list[str]


@dataclass
class SpecialistConfig:
    """Configuration for a specialist agent."""

    name: str
    description: str
    version: str = '0.1.0'
    system_prompt: str = ''
    skills: list[SpecialistSkillSpec] = field(default_factory=list)
    tavily_tool_name: str = 'research_web'
    tavily_tool_description: str = 'Search the web for up-to-date information.'
    table_name_prefix: str = 'specialist'
    artifact_name: str = 'specialist-response'
    agent_name_label: str = ''

    @property
    def tasks_table(self) -> str:
        return f'{self.table_name_prefix}_tasks'

    @property
    def push_notification_table(self) -> str:
        return f'{self.table_name_prefix}_push_notification_configs'


# ---------------------------------------------------------------------------
# Generic specialist executor
# ---------------------------------------------------------------------------


def _agent_message(*, task_id: str, context_id: str, text: str) -> Message:
    return Message(
        role=Role.ROLE_AGENT,
        task_id=task_id,
        context_id=context_id,
        parts=[Part(text=text)],
    )


class GenericSpecialistExecutor(AgentExecutor):
    """Executor that runs a LangChain create_agent with optional Tavily tool."""

    def __init__(
        self,
        settings: Settings,
        config: SpecialistConfig,
        *,
        checkpointer: Any,
    ) -> None:
        self.settings = settings
        self.config = config
        self.checkpointer = checkpointer
        self.agent = None

    def _get_tools(self) -> list[Any]:
        tools: list[Any] = []
        if self.settings.tavily_configured:
            tools.append(
                build_tavily_research_tool(
                    self.settings,
                    tool_name=self.config.tavily_tool_name,
                    tool_description=self.config.tavily_tool_description,
                )
            )
        return tools

    def _get_agent(self):
        if self.agent is None:
            self.agent = create_agent(
                model=build_chat_model(self.settings, streaming=True),
                tools=self._get_tools(),
                system_prompt=self.config.system_prompt,
                checkpointer=self.checkpointer,
                name=f'nimbus-{self.config.table_name_prefix}',
            )
        return self.agent

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

        label = self.config.agent_name_label or self.config.name
        updater = TaskUpdater(event_queue, task_id, context_id)
        await updater.start_work(
            _agent_message(
                task_id=task_id,
                context_id=context_id,
                text=f'{label} received the request and is preparing a response.',
            )
        )

        artifact_id = f'{task_id}-{self.config.table_name_prefix}-response'
        first_chunk = True

        # Buffer tokens and flush in batches to avoid one push notification
        # (HTTP POST to the orchestrator webhook) per token. We flush when the
        # buffer exceeds FLUSH_CHARS OR FLUSH_INTERVAL seconds have elapsed.
        FLUSH_CHARS = 2000
        FLUSH_INTERVAL = 2.0
        buffer: list[str] = []
        buffer_len = 0
        last_flush = time.monotonic()

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
            buffer.append(text)
            buffer_len += len(text)
            await flush()

        # Flush any remaining buffered text.
        await flush(force=True)

        await updater.complete(
            _agent_message(
                task_id=task_id,
                context_id=context_id,
                text=f'{label} completed the response.',
            )
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
                text=f'{self.config.name} task cancelled.',
            )
        )


# ---------------------------------------------------------------------------
# Agent card builder
# ---------------------------------------------------------------------------


def build_specialist_agent_card(settings: Settings, config: SpecialistConfig) -> AgentCard:
    """Build an A2A AgentCard from a SpecialistConfig."""
    interface_base_url = settings.specialist_internal_url
    return AgentCard(
        name=config.name,
        description=config.description,
        version=config.version,
        documentation_url=f'{settings.specialist_public_url}/docs',
        provider=AgentProvider(
            organization='Nimbus Chat',
            url=settings.specialist_public_url,
        ),
        capabilities=AgentCapabilities(
            streaming=True,
            push_notifications=True,
            extended_agent_card=False,
        ),
        default_input_modes=['text/plain'],
        default_output_modes=['text/plain'],
        supported_interfaces=[
            AgentInterface(
                url=f'{interface_base_url}/a2a/jsonrpc',
                protocol_binding='JSONRPC',
                protocol_version='1.0',
            ),
            AgentInterface(
                url=f'{interface_base_url}/a2a',
                protocol_binding='HTTP+JSON',
                protocol_version='1.0',
            ),
        ],
        skills=[
            AgentSkill(
                id=skill.id,
                name=skill.name,
                description=skill.description,
                tags=skill.tags,
                examples=skill.examples,
                input_modes=['text/plain'],
                output_modes=['text/plain'],
            )
            for skill in config.skills
        ],
    )
