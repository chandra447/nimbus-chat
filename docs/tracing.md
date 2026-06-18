# Distributed Tracing (HoneyHive)

Nimbus Chat ships with **distributed tracing to [HoneyHive](https://honeyhive.ai)**
so every request — across the orchestrator and all specialists — lands in a
single per-conversation session with a true parent-child trace tree.

## How it works

```
┌─────────────────────────────────────────────────────────────────────┐
│ Orchestrator (LangGraph StateGraph)                                 │
│                                                                     │
│  ┌──────────────────── HoneyHive session ────────────────────────┐  │
│  │ orchestrator_turn span                                         │  │
│  │   ├─ router LLM call        (LangChain instrumented)           │  │
│  │   ├─ call_specialist span ──┐ (inject W3C traceparent +        │  │
│  │   │                         │  HoneyHive session baggage       │  │
│  │   │                         │  into A2A request headers)       │  │
│  │   │   ┌─────────────────────┘                                  │  │
│  │   │   ▼                                                        │  │
│  │   │  ┌─── Specialist (travel) ────────────────────────────┐   │  │
│  │   │  │ with_distributed_trace_context(headers) extracts   │   │  │
│  │   │  │ context → specialist agent spans are CHILDREN       │   │  │
│  │   │  │   ├─ LLM call (LangChain instrumented)              │   │  │
│  │   │  │   └─ Tavily tool call                                │   │  │
│  │   │  └─────────────────────────────────────────────────────┘   │  │
│  │   └─ synthesize / assemble LLM call                            │  │
│  └────────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────┘
```

- **One registered HoneyHive session per conversation**, keyed by a deterministic
  UUID5 of the `context_id`. The orchestrator registers that session with
  HoneyHive via `HoneyHiveTracer.create_session(session_id=..., skip_api_call=False)`
  at the start of the conversation/turn and reuses the same ID across turns,
  so the HoneyHive Sessions UI has a real session root event to attach every
  span to.
- **LangChain is auto-instrumented** via `openinference-langchain` — all
  `create_agent` / model / tool calls become spans automatically. No manual
  span code in the agent logic.
- **A2A's own OpenTelemetry tracing is disabled**
  (`OTEL_INSTRUMENTATION_A2A_SDK_ENABLED=false`) — we only want LangChain
  traces flowing to HoneyHive, not A2A protocol internals.

## Context propagation

The orchestrator → specialist trace link uses HoneyHive's distributed-tracing
helpers. **Two things are required**:

1. The session must be registered with HoneyHive's Sessions API so the UI has
   a session root event. `app/tracing.register_session_with_honeyhive()` calls
   `HoneyHiveTracer.create_session(...)` with Nimbus' deterministic session ID.
   Repeating the API call with the same `session_id` is idempotent in the SDK/API;
   after a successful local registration, later turns use `skip_api_call=True`.
2. The session ID must be placed in OTel baggage. `enrich_span_context(session_id=...)`
   only sets it as a span *attribute*, but the HoneyHive span processor stamps
   every child span by reading the baggage key `honeyhive.session_id`.
   `app/tracing.attach_session_to_context()` sets both `session_id` (for the SDK)
   and `honeyhive.session_id` (for the OTLP span processor), so the session
   propagates to child spans *and* across the A2A boundary.

| Side | Helper | What it does |
|------|--------|--------------|
| **Orchestrator** (client) | `register_session_with_honeyhive()` + `enrich_span_context("orchestrator_turn")` + `attach_session_to_context()` + `inject_context_into_carrier(headers, tracer)` | Registers/links the deterministic session with HoneyHive, wraps the turn in a span, puts `session_id` + `honeyhive.session_id` into baggage (so the orchestrator's own router/responder/synthesizer spans are stamped), and injects the W3C `traceparent` + session baggage into the outgoing A2A request headers (via `ClientCallContext.service_parameters`). |
| **Specialist** (server, receive) | `with_distributed_trace_context(headers, tracer)` | A FastAPI middleware extracts the incoming trace context + baggage and attaches it — so all LangChain spans created while handling the request become **children** of the orchestrator's `call_specialist` span, in the **same session**. |
| **Specialist** (server, push back) | `_TracingPushNotificationSender` | Subclass of `BasePushNotificationSender` that injects the current trace context into the callback POST headers. The push-notification producer task inherits the baggage from the receive middleware, so the callback carries the session back. |
| **Orchestrator** (webhook, receive) | `with_distributed_trace_context(headers, tracer, session_id=...)` | The `/a2a/callback` webhook extracts the context the specialist injected, so relaying chunks + resuming the graph interrupt lands in the same trace + session. |

## Configuration

All tracing is env-gated and **off by default** (no API key = no-op, the app
runs normally). Set these in `backend/.env` (loaded into containers via
`env_file: backend/.env` in `docker-compose.yml`):

```bash
# Required to enable tracing. Without it, tracing is a complete no-op.
HH_API_KEY=hh_your_api_key_here

# Optional — HoneyHive infers the project from the API key, but you can name it.
HH_PROJECT=NIMBUS

# Optional on/off switch (defaults on when HH_API_KEY is set).
HH_ENABLE_TRACING=true

# Disables the A2A SDK's own OpenTelemetry tracing — we only want LangChain
# traces in HoneyHive, not A2A protocol spans.
OTEL_INSTRUMENTATION_A2A_SDK_ENABLED=false
```

After setting these, rebuild and send a request — check the HoneyHive dashboard
under your project's **Sessions** view. Each conversation's session ID is
`uuid5(NAMESPACE_DNS, context_id)`, deterministic and stable across turns. The
session should contain the `orchestrator_turn` root span, auto-instrumented
LangChain spans, and each specialist's spans under the corresponding
`call_specialist` dispatch span.

For local debugging only, set `HH_DEBUG=true` to attach a console span exporter
that prints span names and `honeyhive.session_id` values. Do not leave it on in
normal development or production because it is noisy.

## Where the code lives

| File | Responsibility |
|------|----------------|
| `backend/app/tracing.py` | Tracer init (one per service), LangChain instrumentation, A2A tracing disable, session-ID derivation, `register_session_with_honeyhive()` (HoneyHive Sessions API registration/linking), `attach_session_to_context()` (baggage propagation), `inject_trace_context_into_headers()`, known-error suppression. |
| `backend/app/orchestrator/session.py` | `GraphSession` registers/links the HoneyHive session, wraps each turn in `enrich_span_context` + `attach_session_to_context` (session in baggage), injects trace context into the A2A dispatch headers, and exposes `_callback_trace_context()` for the webhook to extract the specialist's callback context. |
| `backend/nimbus_a2a/server.py` | `create_specialist_app(…, tracer=…)` adds a FastAPI middleware that calls `with_distributed_trace_context(headers, tracer)` so specialist spans link to the caller, and uses `_TracingPushNotificationSender` to inject trace context into callback POSTs. |
| `backend/app/orchestrator/service.py` | The `/a2a/callback` webhook wraps handling in `session._callback_trace_context(request)` so resume processing joins the original trace + session. |
| `backend/app/specialist/service.py` | Passes `get_tracer(f'specialist:{prefix}')` into the SDK. |

## For SDK teams (specialist authors)

If you're building a specialist with the `nimbus_a2a` SDK and want distributed
tracing, pass a HoneyHive tracer to `create_specialist_app`:

```python
from nimbus_a2a import create_specialist_app, SpecialistServerConfig
from honeyhive import HoneyHiveTracer
from openinference.instrumentation.langchain import LangChainInstrumentor

tracer = HoneyHiveTracer.init(api_key=os.environ["HH_API_KEY"], source="my-specialist")
LangChainInstrumentor().instrument(tracer_provider=tracer.provider)

app = create_specialist_app(
    executor,
    agent_card,
    server=server_config,
    tracer=tracer,   # ← enables distributed-trace context extraction
)
```

The SDK handles the rest: incoming A2A requests are wrapped in
`with_distributed_trace_context`, so your specialist's spans automatically link
to the orchestrator's trace and land in the same HoneyHive session. The
push-notification sender also injects the trace context into callback POSTs,
so the orchestrator's webhook resume processing joins the same trace.

## Known issues

- **`OpenInferenceTracer` + LangGraph interrupts**: the openinference-langchain
  tracer doesn't implement LangGraph's `on_interrupt`/`on_resume` callbacks
  ([openinference #3231](https://github.com/Arize-ai/openinference/issues/3231)).
  This produces noisy WARNING logs. `app/tracing.py` raises the
  `langchain_core.callbacks.manager` logger to ERROR level to suppress them.
  The LLM/tool spans are still recorded correctly — only the interrupt/resume
  *events* are missed.
