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
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Deterministic namespace for UUID5 derivation of session IDs from context IDs.
_SESSION_NAMESPACE = uuid.UUID('6ba7b810-9dad-11d1-80b4-00c04fd430c8')  # NAMESPACE_DNS

_tracers: dict[str, Any] = {}  # source -> HoneyHiveTracer (one per service)
_langchain_instrumented = False


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


def shutdown_tracers() -> None:
    """Flush + shutdown all tracers (call on app shutdown)."""
    for tracer in _tracers.values():
        try:
            tracer.flush()
            tracer.shutdown()
        except Exception:  # noqa: BLE001
            pass
    _tracers.clear()
