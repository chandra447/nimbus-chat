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

- **One HoneyHive session per conversation**, keyed by a deterministic UUID5
  of the `context_id`. Every turn (and every specialist delegation within a
  turn) maps to the same session, so you get a unified timeline.
- **LangChain is auto-instrumented** via `openinference-langchain` — all
  `create_agent` / model / tool calls become spans automatically. No manual
  span code in the agent logic.
- **A2A's own OpenTelemetry tracing is disabled**
  (`OTEL_INSTRUMENTATION_A2A_SDK_ENABLED=false`) — we only want LangChain
  traces flowing to HoneyHive, not A2A protocol internals.

## Context propagation

The orchestrator → specialist trace link uses HoneyHive's distributed-tracing
helpers:

| Side | Helper | What it does |
|------|--------|--------------|
| **Orchestrator** (client) | `enrich_span_context("call_specialist")` + `inject_context_into_carrier(headers, tracer)` | Wraps the A2A dispatch in a span and injects the W3C `traceparent` + session/project `baggage` into the outgoing A2A request headers (via `ClientCallContext.service_parameters`). |
| **Specialist** (server) | `with_distributed_trace_context(headers, tracer)` | A FastAPI middleware extracts the incoming trace context and attaches it as the current OTel context — so all LangChain spans created while handling the request become **children** of the orchestrator's `call_specialist` span, in the **same session**. |

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
under your project. Each conversation's session ID is
`uuid5(NAMESPACE_DNS, context_id)`, deterministic and stable across turns.

## Where the code lives

| File | Responsibility |
|------|----------------|
| `backend/app/tracing.py` | Tracer init (one per service), LangChain instrumentation, A2A tracing disable, session-ID derivation, known-error suppression. |
| `backend/app/orchestrator/session.py` | `GraphSession` wraps each turn in `enrich_span_context(session_id=…)` and injects trace context into the A2A dispatch headers. |
| `backend/nimbus_a2a/server.py` | `create_specialist_app(…, tracer=…)` adds a FastAPI middleware that calls `with_distributed_trace_context(headers, tracer)` so specialist spans link to the caller. |
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
to the orchestrator's trace and land in the same HoneyHive session.

## Known issues

- **`OpenInferenceTracer` + LangGraph interrupts**: the openinference-langchain
  tracer doesn't implement LangGraph's `on_interrupt`/`on_resume` callbacks
  ([openinference #3231](https://github.com/Arize-ai/openinference/issues/3231)).
  This produces noisy WARNING logs. `app/tracing.py` raises the
  `langchain_core.callbacks.manager` logger to ERROR level to suppress them.
  The LLM/tool spans are still recorded correctly — only the interrupt/resume
  *events* are missed.
