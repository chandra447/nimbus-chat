"""nimbus_a2a — a pure A2A specialist SDK.

Specialist teams subclass :class:`SpecialistExecutor`, override ``invoke()`` or
``stream()`` with their own agent logic (any framework — LangChain, LangGraph,
Pydantic AI, DSPy, raw LLM calls, …), build their own A2A ``AgentCard``, and
call :func:`create_specialist_app` to get a FastAPI app. The SDK owns all A2A
protocol, task lifecycle, and ``return_immediately`` + push-notification
plumbing. Teams never touch A2A internals.

Example::

    from a2a.types import AgentCard
    from a2a.types.a2a_pb2 import AgentCapabilities, AgentInterface, AgentSkill
    from nimbus_a2a import SpecialistExecutor, SpecialistServerConfig, create_specialist_app

    class MyExecutor(SpecialistExecutor):
        def __init__(self):
            super().__init__(streaming=True, artifact_name='my-response', label='My Agent')

        async def stream(self, message, *, context_id):
            # ...your framework's streaming logic, yielding text chunks...
            async for chunk in my_agent.astream(message):
                yield chunk

    card = AgentCard(
        name='My Specialist',
        description='Does something useful.',
        capabilities=AgentCapabilities(push_notifications=True),
        skills=[AgentSkill(id='x', name='X', description='...')],
        supported_interfaces=[AgentInterface(url='http://localhost:8003/a2a/jsonrpc', ...)],
        ...
    )

    app = create_specialist_app(
        MyExecutor(),
        card,
        server=SpecialistServerConfig(
            db_url='sqlite+aiosqlite:///./data/nimbus-chat.db',
            public_url='http://localhost:8003',
            internal_url='http://my-specialist:8003',
            tasks_table='my_specialist_tasks',
            push_notification_table='my_specialist_push_configs',
        ),
    )
    # uvicorn.run(app, host='0.0.0.0', port=8003)

The ``return_immediately`` + push-notification pattern is handled for you: when
the orchestrator submits a task with ``return_immediately=True`` and a push
config, the specialist processes in the background and POSTs status/artifact
events to the orchestrator's webhook. Streaming specialists (``streaming=True``)
push buffered incremental artifact chunks; non-streaming specialists
(``streaming=False``) push one final artifact.
"""

from nimbus_a2a.executor import SpecialistExecutor
from nimbus_a2a.server import SpecialistServerConfig, create_specialist_app

__all__ = [
    'SpecialistExecutor',
    'SpecialistServerConfig',
    'create_specialist_app',
]
