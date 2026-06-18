from __future__ import annotations

from typing import Any

from langchain.agents import create_agent

from a2a.helpers import new_task_from_user_message
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events.event_queue import EventQueue
from a2a.server.tasks.task_updater import TaskUpdater
from a2a.types.a2a_pb2 import Message, Part, Role, TaskState

from app.llm import build_chat_model
from app.settings import Settings
from app.specialist.tavily import build_tavily_research_tool
from app.specialist.workflow import build_travel_planning_graph


def agent_message(*, task_id: str, context_id: str, text: str) -> Message:
    return Message(
        role=Role.ROLE_AGENT,
        task_id=task_id,
        context_id=context_id,
        parts=[Part(text=text)],
    )


class TravelSpecialistExecutor(AgentExecutor):
    def __init__(self, settings: Settings, *, checkpointer: Any) -> None:
        self.settings = settings
        self.graph = build_travel_planning_graph()
        self.checkpointer = checkpointer
        self.agent = None

    def _get_tools(self):
        return [build_tavily_research_tool(self.settings)]

    def _get_agent(self):
        if self.agent is None:
            tools = self._get_tools()
            self.agent = create_agent(
                model=build_chat_model(self.settings, streaming=True),
                tools=tools,
                system_prompt=(
                    'You are Nimbus Travel Planner, a travel specialist agent. '
                    'Provide practical travel help. When useful, structure the answer '
                    'with sections like Overview, Suggested Itinerary, Budget Notes, '
                    'Activities, and Travel Tips. Be transparent when making assumptions. '
                    'If live research context is provided, use it carefully and cite the '
                    'facts in plain language.'
                ),
                checkpointer=self.checkpointer,
                name='nimbus-travel-specialist',
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
            await event_queue.enqueue_event(
                new_task_from_user_message(context.message)
            )

        updater = TaskUpdater(event_queue, task_id, context_id)
        await updater.start_work(
            agent_message(
                task_id=task_id,
                context_id=context_id,
                text='Travel specialist received the request and is preparing a planning brief.',
            )
        )

        workflow_state = await self.graph.ainvoke({'user_input': user_input})
        travel_brief = workflow_state.get('travel_brief', user_input)

        await updater.update_status(
            TaskState.TASK_STATE_WORKING,
            message=agent_message(
                task_id=task_id,
                context_id=context_id,
                text='Generating itinerary, budget suggestions, and activity recommendations.',
            ),
        )

        artifact_id = f'{task_id}-travel-response'
        user_prompt = travel_brief
        first_chunk = True
        async for event in self._get_agent().astream_events(
            {
                'messages': [
                    {
                        'role': 'user',
                        'content': user_prompt,
                    }
                ]
            },
            config={
                'configurable': {'thread_id': f'{context_id}:travel'},
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
            await updater.add_artifact(
                artifact_id=artifact_id,
                name='travel-plan',
                parts=[Part(text=text)],
                append=not first_chunk,
                last_chunk=False,
                metadata={'source': 'travel-specialist'},
            )
            first_chunk = False

        await updater.complete(
            agent_message(
                task_id=task_id,
                context_id=context_id,
                text='Travel specialist completed the response.',
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
                text='Travel specialist task cancelled.',
            )
        )
