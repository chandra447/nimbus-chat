"""The orchestrator as a LangGraph StateGraph.

The orchestrator is no longer an A2A server. It is a LangGraph ``StateGraph``
(checkpointed to SQLite) that:

1. **Routes** the user message (router agent → structured ``RouteDecision``).
2. **Fans out** to one or more specialists via ``Send``. Each ``specialist_wait``
   task calls ``interrupt()`` — the graph *pauses* (state checkpointed) and the
   driver (see ``session.py``) sends an A2A ``SendMessage(return_immediately=True)``
   to the specialist with a push-notification config pointing back to the
   orchestrator webhook.
3. **Waits** for push notifications. As each specialist completes, the webhook
   resumes the corresponding interrupt with ``Command(resume={interrupt_id: response})``.
   When multiple specialists are selected, the graph pauses with *multiple*
   interrupts that are resumed one at a time as each specialist posts back.
4. **Synthesizes** (if ``needs_synthesis``) or **assembles** (section headers,
   no extra LLM call) the specialist responses, or **responds** directly when
   no specialist is needed.

Token streaming for the responder / synthesizer is emitted through LangGraph's
custom stream (``get_stream_writer``) so the driver can relay it to the frontend
SSE stream.
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict

from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, Send, interrupt

from app.orchestrator.routing import (
    OrchestratorResponder,
    OrchestratorRouter,
    OrchestratorSynthesizer,
    RouteDecision,
)


class OrchestratorState(TypedDict, total=False):
    """State for the orchestrator graph."""

    user_input: str
    context_id: str
    # RouteDecision serialised as a dict (so it is checkpoint-friendly).
    route: dict[str, Any] | None
    # (specialist_name, response_text) tuples, aggregated across fan-out tasks.
    specialist_responses: Annotated[list[tuple[str, str]], operator.add]
    final_response: str


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------


async def route_node(state: OrchestratorState) -> Command:
    """Decide routing via the router agent, then fan out or respond directly."""
    writer = get_stream_writer()
    user_input = state['user_input']
    context_id = state['context_id']

    writer({'type': 'status', 'phase': 'routing', 'text': 'Analyzing routing options…'})

    decision: RouteDecision = await _ROUTER.decide(user_input, thread_id=context_id)
    route_dict = decision.model_dump()

    writer(
        {
            'type': 'status',
            'phase': 'route_decision',
            'text': (
                f'Routing to {len(decision.specialists)} specialist(s): '
                f'{", ".join(s.name for s in decision.specialists) or "none"}.'
                + (f' Reason: {decision.rationale}' if decision.rationale else '')
            ),
            'specialists': [s.model_dump() for s in decision.specialists],
            'needs_synthesis': decision.needs_synthesis,
        }
    )

    if not decision.should_route or not decision.specialists:
        return Command(goto='respond', update={'route': route_dict})

    # Fan out: one specialist_wait task per specialist (parallel branches).
    sends = [
        Send(
            'specialist_wait',
            {
                'name': s.name,
                'url': s.url,
                'query': user_input,
                'context_id': context_id,
            },
        )
        for s in decision.specialists
    ]
    return Command(goto=sends, update={'route': route_dict})


async def respond_node(state: OrchestratorState) -> dict[str, Any]:
    """Stream a direct response (no specialist needed)."""
    writer = get_stream_writer()
    writer({'type': 'status', 'phase': 'responding', 'text': 'Responding directly…'})

    full: list[str] = []
    async for token in _RESPONDER.stream_text(
        state['user_input'], thread_id=state['context_id']
    ):
        writer({'type': 'token', 'text': token})
        full.append(token)

    return {'final_response': ''.join(full)}


async def specialist_wait_node(state: dict[str, Any]) -> dict[str, Any]:
    """One fan-out task. Pure interrupt — no side effects before this call.

    The interrupt payload carries everything the driver needs to dispatch the
    A2A request to the specialist. When the specialist completes, the webhook
    resumes this interrupt with the specialist's full response, which becomes
    the return value of ``interrupt()``.

    IMPORTANT: LangGraph re-runs the node from the top on resume, so this node
    must contain NO side effects before ``interrupt()``. The A2A send happens
    in the driver, after the graph pauses.
    """
    payload = {
        'specialist_name': state['name'],
        'specialist_url': state['url'],
        'query': state['query'],
        'context_id': state['context_id'],
    }
    response: str = interrupt(payload)
    return {'specialist_responses': [(state['name'], response)]}


async def synthesize_node(state: OrchestratorState) -> dict[str, Any]:
    """Synthesize multiple specialist responses into one streamed answer."""
    writer = get_stream_writer()
    writer({'type': 'status', 'phase': 'synthesizing', 'text': 'Synthesizing a unified response…'})

    specialist_responses = [
        (name, text) for name, text in state.get('specialist_responses', [])
    ]
    full: list[str] = []
    async for token in _SYNTHESIZER.stream_synthesis(
        state['user_input'], specialist_responses, thread_id=state['context_id']
    ):
        writer({'type': 'token', 'text': token})
        full.append(token)

    return {'final_response': ''.join(full)}


async def assemble_node(state: OrchestratorState) -> dict[str, Any]:
    """Assemble specialist responses with section headers (no extra LLM call)."""
    writer = get_stream_writer()
    writer({'type': 'status', 'phase': 'assembling', 'text': 'Assembling specialist responses…'})

    parts: list[str] = []
    for name, text in state.get('specialist_responses', []):
        parts.append(f'\n\n## {name}\n\n')
        parts.append(text)
    full = ''.join(parts).strip()

    # Stream it in small chunks so the frontend renders progressively.
    for i in range(0, len(full), 40):
        writer({'type': 'token', 'text': full[i : i + 40]})

    return {'final_response': full}


# ---------------------------------------------------------------------------
# Edges
# ---------------------------------------------------------------------------


def after_specialists(state: OrchestratorState) -> str:
    """After all specialist_wait tasks complete: synthesize or assemble."""
    route = state.get('route') or {}
    if route.get('needs_synthesis') and len(route.get('specialists', [])) >= 2:
        return 'synthesize'
    return 'assemble'


# ---------------------------------------------------------------------------
# Module-level agent handles (injected by build_orchestrator_graph)
# ---------------------------------------------------------------------------

_ROUTER: OrchestratorRouter | None = None
_RESPONDER: OrchestratorResponder | None = None
_SYNTHESIZER: OrchestratorSynthesizer | None = None


def build_orchestrator_graph(
    *,
    router: OrchestratorRouter,
    responder: OrchestratorResponder,
    synthesizer: OrchestratorSynthesizer,
    checkpointer: Any,
) -> Any:
    """Compile the orchestrator StateGraph with the given agents + checkpointer."""
    global _ROUTER, _RESPONDER, _SYNTHESIZER
    _ROUTER = router
    _RESPONDER = responder
    _SYNTHESIZER = synthesizer

    builder = StateGraph(OrchestratorState)
    builder.add_node('route', route_node)
    builder.add_node('respond', respond_node)
    builder.add_node('specialist_wait', specialist_wait_node)
    builder.add_node('synthesize', synthesize_node)
    builder.add_node('assemble', assemble_node)

    builder.add_edge(START, 'route')
    builder.add_edge('respond', END)
    # Fan-out barrier: after ALL specialist_wait tasks complete, route to synthesize/assemble.
    builder.add_conditional_edges('specialist_wait', after_specialists, ['synthesize', 'assemble'])
    builder.add_edge('synthesize', END)
    builder.add_edge('assemble', END)

    return builder.compile(checkpointer=checkpointer)
