"""Distributed tracing to HoneyHive.

This module wires up HoneyHive tracing for both the orchestrator and the
specialists. The design:

- **One tracer per service** (orchestrator, each specialist), created lazily on
  first use and cached. LangChain is instrumented once globally via
  ``openinference-langchain`` so all ``create_agent`` / model / tool calls are
  auto-traced. A2A's own OpenTelemetry tracing is disabled
  (``OTEL_INSTRUMENTATION_A2A_SDK_ENABLED=false``) — we only want LangChain
  traces flowing to HoneyHive.

- **One HoneyHive session per conversation**, keyed by a deterministic UUID5
  of the ``context_id``. The session ID propagates across services via W3C
  baggage (injected by :func:`inject_context_into_carrier` on the orchestrator
  side, extracted by :func:`with_distributed_trace_context` on the specialist
  side), so the orchestrator's routing/response spans and every specialist's
  agent spans all land in the same session — with the specialist spans as
  children of the orchestrator's dispatch span.

Env vars:
- ``HH_API_KEY``      — HoneyHive API key (required for tracing).
- ``HH_PROJECT``      — project name (optional; HoneyHive infers from the key).
- ``HH_ENABLE_TRACING`` — explicit on/off (defaults on when ``HH_API_KEY`` set).
- ``OTEL_INSTRUMENTATION_A2A_SDK_ENABLED=false`` — disable A2A's own tracing.
"""

from __future__ import annotations

import logging
import os
import uuid
from contextlib import contextmanager
from typing import Any, Iterator, Optional

logger = logging.getLogger(__name__)

# Deterministic namespace for UUID5 derivation of session IDs from context IDs.
_SESSION_NAMESPACE = uuid.UUID('6ba7b810-9dad-11d1-80b4-00c04fd430c8')  # NAMESPACE_DNS

_tracers: dict[str, Any] = {}  # source -> HoneyHiveTracer (one per service)
_langchain_instrumented = False
_registered_sessions: set[str] = set()


def session_id_for(context_id: str) -> str:
    """Derive a stable HoneyHive session UUID from a conversation context_id.

    HoneyHive requires session IDs to be UUIDs. We use UUID5 so every turn in
    the same conversation maps to the same session — giving a single unified
    trace across the orchestrator and all specialists for that conversation.
    """
    return str(uuid.uuid5(_SESSION_NAMESPACE, context_id))


def is_tracing_enabled() -> bool:
    """Whether HoneyHive tracing is active for this process."""
    enabled = os.getenv('HH_ENABLE_TRACING', '').strip().lower()
    if enabled in ('0', 'false', 'no', 'off'):
        return False
    if enabled in ('1', 'true', 'yes', 'on'):
        return bool(os.getenv('HH_API_KEY'))
    return bool(os.getenv('HH_API_KEY'))


def get_tracer(source: str) -> Optional[Any]:
    """Return the cached HoneyHive tracer for ``source`` (one per service).

    Creates it on first call: initializes the tracer, instruments LangChain
    once (against the tracer's provider), and disables A2A's own tracing.
    Returns ``None`` when tracing is disabled (no API key / explicitly off) —
    callers must no-op gracefully in that case.
    """
    if not is_tracing_enabled():
        return None
    if source in _tracers:
        return _tracers[source]

    try:
        from honeyhive import HoneyHiveTracer
    except ImportError:  # pragma: no cover - honeyhive is optional at runtime
        logger.warning('honeyhive not installed; tracing disabled')
        return None

    api_key = os.getenv('HH_API_KEY')
    kwargs: dict[str, Any] = {
        'api_key': api_key,
        'source': source,
        'disable_http_tracing': True,  # we trace LangChain, not raw HTTP
    }
    project = os.getenv('HH_PROJECT')
    if project:
        kwargs['project'] = project

    tracer = HoneyHiveTracer.init(**kwargs)
    _tracers[source] = tracer

    _instrument_langchain(tracer)
    _disable_a2a_tracing()
    _suppress_known_callback_errors()
    _maybe_add_debug_exporter(tracer)
    logger.info('HoneyHive tracer initialized (source=%s, project=%s)', source, project or '<inferred>')
    return tracer


def _instrument_langchain(tracer: Any) -> None:
    """Instrument LangChain once globally against the HoneyHive tracer provider."""
    global _langchain_instrumented
    if _langchain_instrumented:
        return
    try:
        from openinference.instrumentation.langchain import LangChainInstrumentor

        LangChainInstrumentor().instrument(tracer_provider=tracer.provider)
        _langchain_instrumented = True
        logger.info('LangChain instrumented for HoneyHive tracing')
    except Exception:  # noqa: BLE001
        logger.warning('Failed to instrument LangChain for tracing', exc_info=True)


def _disable_a2a_tracing() -> None:
    """Disable the A2A SDK's own OpenTelemetry tracing.

    We only want LangChain traces (via openinference-langchain) in HoneyHive,
    not the A2A protocol's internal spans. The A2A SDK honors
    ``OTEL_INSTRUMENTATION_A2A_SDK_ENABLED=false``.
    """
    os.environ.setdefault('OTEL_INSTRUMENTATION_A2A_SDK_ENABLED', 'false')


def _maybe_add_debug_exporter(tracer: Any) -> None:
    """When HH_DEBUG=1, print every span to stderr (names + session attr).

    Debug-only: lets us see exactly which spans the server creates and whether
    they carry ``honeyhive.session_id``. Off by default.
    """
    if os.getenv('HH_DEBUG', '').strip().lower() not in ('1', 'true', 'yes'):
        return
    import sys

    from opentelemetry.sdk.trace.export import ConsoleSpanExporter, SimpleSpanProcessor

    processor = SimpleSpanProcessor(ConsoleSpanExporter(out=sys.stderr))
    try:
        tracer.provider.add_span_processor(processor)
        logger.info('HH_DEBUG=1: console span exporter attached')
    except Exception:  # noqa: BLE001
        logger.warning('Failed to attach debug console exporter', exc_info=True)


def _suppress_known_callback_errors() -> None:
    """Silence non-fatal openinference-langchain + LangGraph callback errors.

    The OpenInferenceTracer doesn't implement LangGraph's ``on_interrupt`` /
    ``on_resume`` callbacks (openinference issue #3231). LangGraph's callback
    manager catches the AttributeError and logs it as a WARNING per interrupt/
    resume. The LLM/tool spans are still recorded correctly — only the
    interrupt/resume *events* are missed. Raise the callback-manager logger to
    ERROR level so the noise stays out of the logs.
    """
    logging.getLogger('langchain_core.callbacks.manager').setLevel(logging.ERROR)


def register_session_with_honeyhive(
    tracer: Any,
    *,
    session_id: str,
    session_name: str,
    inputs: Optional[dict[str, Any]] = None,
    metadata: Optional[dict[str, Any]] = None,
) -> bool:
    """Register a deterministic HoneyHive session and set SDK baggage.

    HoneyHive sessions are root events in the UI. Spans that carry a
    ``honeyhive.session_id`` but have no registered session event can be
    exported successfully yet not appear in the Sessions tree. The
    recommended web-server pattern is therefore:

    1. initialize one tracer per process, then
    2. call ``create_session()`` per request/conversation to create the
       backend session event and put the request-scoped ``session_id`` in
       OpenTelemetry baggage.

    Nimbus uses a deterministic UUID5 for each conversation, so only the
    first turn in this process should call the HoneyHive sessions API.
    Later turns call ``create_session(..., skip_api_call=True)`` to link to
    the already-created session without creating duplicates. If the API call
    fails, callers still wrap execution in :func:`attach_session_to_context`
    so spans keep the intended session id and can be recovered once the
    session exists.
    """
    if tracer is None:
        return False

    skip_api_call = session_id in _registered_sessions
    try:
        created_session_id = tracer.create_session(
            session_name=session_name,
            session_id=session_id,
            inputs=inputs,
            metadata=metadata,
            skip_api_call=skip_api_call,
        )
    except Exception:  # noqa: BLE001 - tracing must never break a turn
        logger.warning(
            'Failed to register HoneyHive session %s', session_id, exc_info=True
        )
        return False

    if created_session_id:
        _registered_sessions.add(session_id)
        if skip_api_call:
            logger.debug(
                'HoneyHive session context attached (session_id=%s)', session_id
            )
        else:
            logger.info('HoneyHive session registered (session_id=%s)', session_id)
        return True

    logger.warning(
        'HoneyHive create_session returned no session_id for %s; '
        'continuing with local baggage only',
        session_id,
    )
    return False


@contextmanager
def attach_session_to_context(session_id: str) -> Iterator[None]:
    """Attach the HoneyHive session_id into the current OTel baggage.

    HoneyHive's ``enrich_span_context(session_id=...)`` sets the session_id as
    a span *attribute* on the span it creates, but it does **not** put it into
    the OTel baggage. The HoneyHive span processor
    (``honeyhive/tracer/core/operations.py``) stamps every child span's
    ``honeyhive.session_id`` attribute by reading the baggage key
    ``honeyhive.session_id`` — and ``inject_context_into_carrier`` only
    propagates baggage, not span attributes. So without explicitly placing the
    session in baggage:

    1. The orchestrator's own child LangChain spans (router / responder /
       synthesizer) are not stamped with the session → they drift into a
       separate session.
    2. ``inject_context_into_carrier`` omits the session from the baggage
       header → the specialist never receives it → specialist spans land in a
       separate session.

    This helper puts both ``session_id`` (read by HoneyHive's dynamic
    enrichment helper) and ``honeyhive.session_id`` (read by the span
    processor) into the current baggage so the session propagates to all
    child spans **and** across the A2A boundary via injected headers. Wrap it
    outside any ``enrich_span_context`` block so the baggage is still present
    when the span ends and HoneyHive's span processor runs.
    """
    from opentelemetry import baggage, context as otel_context

    ctx = otel_context.get_current()
    ctx = baggage.set_baggage('session_id', session_id, ctx)
    ctx = baggage.set_baggage('honeyhive.session_id', session_id, ctx)
    token = otel_context.attach(ctx)
    try:
        yield
    finally:
        otel_context.detach(token)


def inject_trace_context_into_headers(
    headers: dict[str, str], tracer: Any
) -> bool:
    """Inject the current W3C trace context + HoneyHive baggage into ``headers``.

    Thin wrapper around HoneyHive's ``inject_context_into_carrier`` used by the
    push-notification sender (and any other outbound callback) so the receiver
    can attach its processing to the same trace + session. Returns ``True`` if
    injection produced any headers, ``False`` if tracing is inactive.
    """
    if tracer is None:
        return False
    try:
        from honeyhive.tracer.processing.context import (
            inject_context_into_carrier,
        )

        before = len(headers)
        inject_context_into_carrier(headers, tracer)
        return len(headers) > before
    except Exception:  # noqa: BLE001 - never let tracing break the callback
        return False


def shutdown_tracers() -> None:
    """Flush + shutdown all tracers (call on app shutdown)."""
    for tracer in _tracers.values():
        try:
            tracer.flush()
            tracer.shutdown()
        except Exception:  # noqa: BLE001
            pass
    _tracers.clear()
