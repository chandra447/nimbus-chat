"""LangChain-based specialist executor (app-level, built on the nimbus_a2a SDK).

This is the Nimbus Chat app's choice of agent framework: LangChain
``create_agent`` + a Tavily research tool + a LangGraph SQLite checkpointer for
memory. It subclasses the SDK's :class:`SpecialistExecutor` and implements
:meth:`stream` — the SDK then handles all A2A protocol, task lifecycle, and
push-notification chunking (streaming vs non-streaming).

A specialist team using a different framework (Pydantic AI, DSPy, raw calls…)
would write their own equivalent subclass — the SDK makes no assumptions about
LangChain or Tavily.
"""

from __future__ import annotations

from typing import Any, AsyncIterator, Awaitable, Callable

from langchain.agents import create_agent

from app.specialist.config import SpecialistConfig
from app.specialist.tavily import build_tavily_research_tool
from nimbus_a2a import SpecialistExecutor

# An async callable returning (connection, checkpointer); the connection is
# closed in shutdown().
CheckpointerFactory = Callable[[], Awaitable[tuple[Any, Any]]]


class LangChainSpecialistExecutor(SpecialistExecutor):
    """Runs a LangChain ``create_agent`` with Tavily research, exposed via A2A."""

    def __init__(
        self,
        config: SpecialistConfig,
        *,
        model: Any,
        checkpointer_factory: CheckpointerFactory,
        tavily_api_key: str = '',
        tavily_enabled: bool = False,
    ) -> None:
        super().__init__(
            streaming=config.streaming,
            artifact_name=config.artifact_name,
            label=config.label or config.name,
        )
        self.config = config
        self.model = model
        self._checkpointer_factory = checkpointer_factory
        self._checkpointer: Any = None
        self._connection: Any = None
        self.tavily_api_key = tavily_api_key
        self.tavily_enabled = tavily_enabled
        self._agent: Any = None

    # ------------------------------------------------------------------
    # Lifecycle (async resources — built in startup, closed in shutdown)
    # ------------------------------------------------------------------

    async def startup(self) -> None:
        self._connection, self._checkpointer = await self._checkpointer_factory()

    async def shutdown(self) -> None:
        if self._connection is not None:
            await self._connection.close()

    # ------------------------------------------------------------------
    # Agent construction
    # ------------------------------------------------------------------

    def _build_tools(self) -> list[Any]:
        tools: list[Any] = []
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
            self._agent = create_agent(
                model=self.model,
                tools=self._build_tools(),
                system_prompt=self.config.system_prompt,
                checkpointer=self._checkpointer,
                name=f'nimbus-{self.config.table_name_prefix}',
            )
        return self._agent

    # ------------------------------------------------------------------
    # Agent logic (streaming mode). In non-streaming mode the SDK's default
    # invoke() collects this.
    # ------------------------------------------------------------------

    async def stream(self, message: str, *, context_id: str) -> AsyncIterator[str]:
        async for event in self._get_agent().astream_events(
            {
                'messages': [
                    {
                        'role': 'user',
                        'content': message,
                    }
                ]
            },
            config={
                'configurable': {
                    'thread_id': f'{context_id}:{self.config.table_name_prefix}'
                },
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
