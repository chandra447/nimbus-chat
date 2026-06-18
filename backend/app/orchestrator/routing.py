from __future__ import annotations

from typing import Any

from langchain.agents import create_agent
from langchain_core.messages import AIMessage, HumanMessage
from pydantic import BaseModel, Field

from app.llm import build_chat_model
from app.orchestrator.middleware import RegisteredSpecialistPromptMiddleware
from app.orchestrator.registry import SpecialistRegistry
from app.settings import Settings


# ---------------------------------------------------------------------------
# Routing models
# ---------------------------------------------------------------------------


class SpecialistRoute(BaseModel):
    """A single specialist selected by the router for fan-out."""

    name: str = Field(description='The name of the specialist agent to route to, exactly as shown in the registered specialist list.')
    url: str = Field(description='The URL of the specialist agent, exactly as shown in the registered specialist list.')
    rationale: str = Field(default='', description='Why this specialist is relevant to this request.')


class RouteDecision(BaseModel):
    """Router decision: route to 0, 1, or multiple specialists."""

    should_route: bool = Field(
        description=(
            'Set to True if ANY registered specialist is relevant to the user request. '
            'Set to False only if NO specialist is relevant at all.'
        ),
    )
    specialists: list[SpecialistRoute] = Field(
        default_factory=list,
        description=(
            'List of specialists to route to. Include ALL specialists whose skills, tags, '
            'or examples are relevant. Can be 0 (no routing), 1 (single routing), or 2+ '
            '(parallel fan-out). When multiple are selected, the orchestrator calls them '
            'all in parallel.'
        ),
    )
    needs_synthesis: bool = Field(
        default=False,
        description=(
            'Only relevant when 2+ specialists are selected. Set to True if the '
            "specialists' advice overlaps, conflicts, or needs to be reconciled into "
            'a single unified answer (e.g. a budget constraint that spans both domains). '
            'Set to False if the specialists address independent aspects of the request '
            'and their responses can be presented side-by-side without reconciliation '
            '(e.g. a travel itinerary + a separate meal plan). When False, each '
            "specialist's response is streamed directly with a section header — no extra LLM call."
        ),
    )
    rationale: str = Field(default='')

    @property
    def specialist_urls(self) -> list[str]:
        return [s.url for s in self.specialists]


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------


class OrchestratorRouter:
    def __init__(
        self,
        settings: Settings,
        registry: SpecialistRegistry,
        *,
        checkpointer: Any,
    ) -> None:
        self.settings = settings
        self.registry = registry
        self.middleware = RegisteredSpecialistPromptMiddleware(registry)
        self.checkpointer = checkpointer
        self._routing_agent = None

    def _get_routing_agent(self):
        if self._routing_agent is None:
            self._routing_agent = create_agent(
                model=build_chat_model(self.settings, streaming=False),
                tools=[],
                system_prompt=(
                    'You are the orchestrator router. Your job is to decide which '
                    'specialist agent(s) should handle the user request, based solely on the '
                    'registered specialist agent cards injected as context. Each specialist '
                    'card lists its name, URL, skills, tags, and examples — use these to '
                    'judge relevance.\n\n'
                    'ROUTING RULES:\n'
                    '1. Select EVERY specialist whose skills, tags, or examples are relevant '
                    'to the user request. The orchestrator fans out to all selected '
                    'specialists in parallel, so selecting multiple is encouraged when a '
                    'request spans multiple domains.\n'
                    '2. If no registered specialist is relevant, set should_route=false so the '
                    'orchestrator answers directly.\n\n'
                    'SYNTHESIS DECISION (only when 2+ specialists are selected):\n'
                    '- Set needs_synthesis=true if the specialists\' advice OVERLAPS or CONFLICTS '
                    'and needs reconciliation into a single answer (e.g. a shared constraint '
                    'spanning their domains).\n'
                    '- Set needs_synthesis=false if the specialists address INDEPENDENT aspects '
                    'that can be presented side-by-side without reconciliation. This skips an '
                    'extra LLM call.\n'
                    '- When in doubt, prefer false.\n\n'
                    'IMPORTANT: When selecting specialists, use the EXACT name and URL from the '
                    'registered specialist list. Do not make up names or URLs. Never pick just one '
                    'when multiple are clearly relevant.'
                ),
                middleware=[self.middleware],
                response_format=RouteDecision,
                checkpointer=self.checkpointer,
                name='nimbus-router',
            )
        return self._routing_agent

    async def decide(self, user_input: str, *, thread_id: str) -> RouteDecision:
        result: dict[str, Any] = await self._get_routing_agent().ainvoke(
            {
                'messages': [
                    {
                        'role': 'user',
                        'content': user_input,
                    }
                ]
            },
            config={
                'configurable': {'thread_id': f'{thread_id}:route'},
                'recursion_limit': 10,
            },
        )
        structured = result.get('structured_response')
        if isinstance(structured, RouteDecision):
            return structured
        if isinstance(structured, dict):
            return RouteDecision.model_validate(structured)
        return RouteDecision(should_route=False, rationale='No route decision returned.')


# ---------------------------------------------------------------------------
# Responder (direct responses)
# ---------------------------------------------------------------------------


class OrchestratorResponder:
    def __init__(self, settings: Settings, *, checkpointer: Any) -> None:
        self.settings = settings
        self.checkpointer = checkpointer
        self._agent = None

    def _get_agent(self):
        if self._agent is None:
            self._agent = create_agent(
                model=build_chat_model(self.settings, streaming=True),
                tools=[],
                system_prompt=(
                    'You are the Nimbus orchestrator assistant. Answer directly when no '
                    'specialist routing is required. Keep responses helpful and concise.'
                ),
                checkpointer=self.checkpointer,
                name='nimbus-orchestrator-responder',
            )
        return self._agent

    async def stream_text(self, user_input: str, *, thread_id: str):
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
                'configurable': {'thread_id': f'{thread_id}:respond'},
                'recursion_limit': 10,
            },
            version='v2',
        ):
            if event.get('event') != 'on_chat_model_stream':
                continue
            chunk = event.get('data', {}).get('chunk')
            text = getattr(chunk, 'text', None)
            if text is not None:
                if callable(text):
                    text = text()
                if text:
                    yield text

    async def record_exchange(
        self,
        *,
        user_input: str,
        assistant_response: str,
        thread_id: str,
    ) -> None:
        """Inject a user + assistant exchange into the responder's thread.

        This lets the orchestrator remember specialist-routed turns so that
        subsequent direct (non-routed) responses have full conversation context.
        Uses ``aupdate_state`` so no LLM call is made.
        """
        await self._get_agent().aupdate_state(
            config={
                'configurable': {'thread_id': f'{thread_id}:respond'},
                'recursion_limit': 10,
            },
            values={'messages': [HumanMessage(user_input), AIMessage(assistant_response)]},
            as_node='model',
        )


# ---------------------------------------------------------------------------
# Synthesizer (combines multiple specialist responses into one)
# ---------------------------------------------------------------------------


class OrchestratorSynthesizer:
    """Synthesizes responses from multiple specialists into a unified answer."""

    def __init__(self, settings: Settings, *, checkpointer: Any) -> None:
        self.settings = settings
        self.checkpointer = checkpointer
        self._agent = None

    def _get_agent(self):
        if self._agent is None:
            self._agent = create_agent(
                model=build_chat_model(self.settings, streaming=True),
                tools=[],
                system_prompt=(
                    'You are the Nimbus orchestrator synthesizer. You receive responses '
                    'from multiple specialist agents and must synthesize them into a '
                    'single, coherent, well-structured response to the user.\n\n'
                    'Guidelines:\n'
                    '- Preserve key insights from each specialist.\n'
                    '- If specialists overlap, reconcile differences and present the '
                    'best combined advice.\n'
                    '- If they complement each other, integrate them naturally.\n'
                    '- Attribute specific advice to the relevant specialist when helpful '
                    '(e.g. "From a nutrition perspective..." or "For your travel plans...").\n'
                    '- Do not invent information not present in the specialist responses.\n'
                    '- Structure the response clearly with headers and sections.'
                ),
                checkpointer=self.checkpointer,
                name='nimbus-synthesizer',
            )
        return self._agent

    async def stream_synthesis(
        self,
        user_input: str,
        specialist_responses: list[tuple[str, str]],
        *,
        thread_id: str,
    ):
        """Stream a synthesized response from multiple specialist outputs.

        Args:
            user_input: The original user request.
            specialist_responses: List of (specialist_name, response_text) tuples.
            thread_id: The conversation thread ID.
        """
        parts = [f'User request: {user_input}\n']
        for name, response in specialist_responses:
            parts.append(f'\n--- Response from {name} ---\n{response}\n')
        parts.append(
            '\n---\nSynthesize the above specialist responses into a single, '
            'coherent response that fully addresses the user request.'
        )
        prompt = '\n'.join(parts)

        async for event in self._get_agent().astream_events(
            {
                'messages': [
                    {
                        'role': 'user',
                        'content': prompt,
                    }
                ]
            },
            config={
                'configurable': {'thread_id': f'{thread_id}:synthesize'},
                'recursion_limit': 10,
            },
            version='v2',
        ):
            if event.get('event') != 'on_chat_model_stream':
                continue
            chunk = event.get('data', {}).get('chunk')
            text = getattr(chunk, 'text', None)
            if text is not None:
                if callable(text):
                    text = text()
                if text:
                    yield text
