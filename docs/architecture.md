# Architecture Deep Dive

## Overview

Nimbus Chat is a multi-agent system built on the A2A (Agent-to-Agent) protocol. It consists of four services orchestrated via Docker Compose:

| Service | Port | Role |
|---|---|---|
| **Frontend** | 3000 | React SPA — chat UI, specialist management, conversation history |
| **Orchestrator** | 8000 | A2A server + router + responder + synthesizer + specialist registry |
| **Travel Specialist** | 8001 | A2A server + LangChain agent with Tavily research tool |
| **Nutrition Specialist** | 8002 | A2A server + LangChain agent with Tavily research tool |

All backend services share a SQLite database via a Docker volume for task persistence and LangGraph checkpointing.

---

## Component Diagram

```mermaid
graph TB
    subgraph "Frontend (:3000)"
        UI[Chat UI]
        Modal[Specialist Modal]
        A2AClient["@a2a-js/sdk Client"]
    end

    subgraph "Orchestrator (:8000)"
        API[REST API]
        A2AServer[A2A JSON-RPC Server]
        Executor[OrchestratorExecutor]
        Router["Router Agent<br/>create_agent + structured output"]
        Responder["Responder Agent<br/>create_agent + streaming"]
        Synthesizer["Synthesizer Agent<br/>create_agent + streaming"]
        Registry[Specialist Registry]
        MW[Prompt Middleware]
    end

    subgraph "Travel Specialist (:8001)"
        TSExec[GenericSpecialistExecutor]
        TSAgent["create_agent<br/>tools: research_travel"]
    end

    subgraph "Nutrition Specialist (:8002)"
        NSExec[GenericSpecialistExecutor]
        NSAgent["create_agent<br/>tools: research_nutrition"]
    end

    subgraph "Persistence"
        SQLite[("SQLite<br/>/data/nimbus-chat.db")]
    end

    UI --> A2AClient
    A2AClient -->|SSE stream| A2AServer
    Modal -->|REST| API

    A2AServer --> Executor
    Executor --> Router
    Executor --> Responder
    Executor --> Synthesizer
    Router --> MW
    MW --> Registry

    Executor -->|A2A client| TSExec
    Executor -->|A2A client| NSExec
    TSExec --> TSAgent
    NSExec --> NSAgent

    Registry --> SQLite
    A2AServer --> SQLite
    TSExec --> SQLite
    NSExec --> SQLite
```

---

## Orchestrator Internals

### The OrchestratorExecutor

The `OrchestratorExecutor` implements the A2A `AgentExecutor` interface. Its `execute()` method is the entry point for every user message:

```mermaid
flowchart TD
    Start["execute(context, event_queue)"] --> CreateTask["Create A2A Task<br/>if not exists"]
    CreateTask --> StartWork["Emit TASK_STATE_WORKING<br/>'Analyzing routing options'"]
    StartWork --> Route["await router.decide(user_input)"]
    Route --> Decision{"RouteDecision"}

    Decision -->|should_route=false| Direct["💬 Responder Agent<br/>streams direct response"]
    Decision -->|1 specialist| Single["🔀 Route to 1 specialist<br/>async push-notification callback"]
    Decision -->|2+ specialists| FanOut["⚡ Parallel async fan-out<br/>return_immediately + push notifications"]

    FanOut --> Collect["📥 Collect responses<br/>from CallbackManager queues"]
    Collect --> SynthCheck{"needs_synthesis?"}
    SynthCheck -->|Yes| Synth["🧬 Synthesizer Agent<br/>combines responses<br/>streams unified answer"]
    SynthCheck -->|No| Sections["📝 Stream each response<br/>with section header<br/>(no extra LLM call)"]
    Single --> Record["💾 record_exchange<br/>into responder thread"]
    Synth --> Record
    Sections --> Record
    Direct --> Done["✅ Task completed"]
    Record --> Done
```

### Three LangChain Agents

The orchestrator runs three separate `create_agent` instances, each with its own LangGraph thread namespace:

| Agent | Thread ID | Purpose |
|---|---|---|
| **Router** | `{contextId}:route` | Structured output — returns `RouteDecision` with specialist list + `needs_synthesis` flag |
| **Responder** | `{contextId}:respond` | Direct responses when no specialist is needed |
| **Synthesizer** | `{contextId}:synthesize` | Combines multiple specialist responses into one (only when `needs_synthesis=true`) |

All three share the same SQLite checkpointer, so conversation history persists across restarts.

### Router Agent

The router uses `create_agent` with `response_format=RouteDecision` (a Pydantic model):

```python
class RouteDecision(BaseModel):
    should_route: bool
    specialists: list[SpecialistRoute]  # 0, 1, or many
    needs_synthesis: bool               # only when 2+ specialists
    rationale: str
```

The `RegisteredSpecialistPromptMiddleware` injects a formatted list of all registered specialists (with their skills, tags, examples) into the router's system prompt before each call. The router then decides which specialists are relevant and whether their advice overlaps enough to require synthesis.

### Async Push-Notification Pattern

The orchestrator communicates with specialists using the A2A push-notification async pattern (configurable via `ASYNC_SPECIALIST_MODE`):

```mermaid
sequenceDiagram
    participant O as Orchestrator
    participant CM as CallbackManager
    participant S as Specialist
    participant WH as Webhook /a2a/callback

    O->>CM: create_queue(callback_token)
    O->>S: SendMessage(return_immediately=true, push_config=url+token)
    S-->>O: 200 OK (initial Task)
    Note over S: Specialist works in background
    Note over O: Orchestrator freed up

    S->>WH: POST /a2a/callback (StreamResponse: status_update WORKING)
    WH->>CM: push_event(token, event)
    CM->>O: queue.get() → event
    O->>O: relay status to client SSE

    S->>WH: POST /a2a/callback (StreamResponse: status_update COMPLETED)
    WH->>CM: push_event(token, event)
    CM->>O: queue.get() → terminal event
    O->>O: break loop, cleanup queue
```

**How it works:**
1. The executor generates a unique `callback_token` and creates an `asyncio.Queue` in the `CallbackManager`
2. It sends a non-streaming `SendMessage` with `return_immediately=True` and a `TaskPushNotificationConfig` containing the orchestrator webhook URL + token
3. The specialist returns the initial `Task` immediately and continues processing in the background
4. As the specialist works, the A2A SDK's `BasePushNotificationSender` POSTs `StreamResponse` events to `/a2a/callback`
5. The webhook parses the event, extracts the token from the `X-A2A-Notification-Token` header, and pushes it into the corresponding `asyncio.Queue`
6. The executor consumes events from the queue — relaying status updates and collecting artifact chunks
7. When a terminal state arrives (`COMPLETED`/`FAILED`), the executor finishes and cleans up the queue

**Fallback:** When `ASYNC_SPECIALIST_MODE=false`, the executor uses traditional streaming SSE (`send_message_stream`) — simpler but holds the A2A connection open for the entire specialist execution.

### Specialist Prompt Middleware

```python
class RegisteredSpecialistPromptMiddleware(AgentMiddleware):
    async def awrap_model_call(self, request, call_next):
        fragment = await registry.render_prompt_fragment()
        # Inject specialist info into the system message
        request.messages[0].content += "\n\n" + fragment
        return await call_next(request)
```

This ensures the router always has up-to-date information about available specialists.

---

## Specialist Framework

### GenericSpecialistExecutor

All specialists share the same executor code — only the `SpecialistConfig` differs:

```python
@dataclass
class SpecialistConfig:
    name: str
    description: str
    system_prompt: str
    skills: list[SpecialistSkillSpec]
    tavily_tool_name: str
    tavily_tool_description: str
    table_name_prefix: str  # e.g. "travel_specialist"
    artifact_name: str      # e.g. "travel-plan"
```

The executor:
1. Creates a task from the user message (`new_task_from_user_message`)
2. Emits a "received" status update
3. Streams the LangChain agent's output as artifact chunks
4. Emits a completion status

### Tavily Research Tool

Each specialist gets a LangChain `StructuredTool` wrapping Tavily search:

```python
def build_tavily_research_tool(settings, *, tool_name, tool_description):
    def _search(query: str) -> str:
        client = TavilyClient(api_key=settings.tavily_api_key)
        response = client.search(query=query, max_results=5, include_answer=True)
        # Format results...
        return formatted

    return StructuredTool.from_function(
        func=_search,
        name=tool_name,
        description=tool_description,
    )
```

The tool is passed to `create_agent(tools=[...])`, so the LLM can decide when to search the web.

---

## Data Persistence

### SQLite Tables

```mermaid
erDiagram
    specialists ||--|| agent_cards : "has cached"
    tasks ||--o{ task_updates : "has"
    travel_specialist_tasks ||--o{ task_updates : "has"
    nutrition_specialist_tasks ||--o{ task_updates : "has"

    specialists {
        TEXT id PK
        TEXT name
        TEXT url UK
        TEXT description
        TEXT tags_json
        TEXT agent_card_json
        TEXT card_refreshed_at
    }

    tasks {
        TEXT id PK
        TEXT context_id
        TEXT status_json
        TEXT history_json
    }

    travel_specialist_tasks {
        TEXT id PK
        TEXT context_id
        TEXT status_json
        TEXT history_json
    }

    checkpoints {
        TEXT thread_id PK
        INTEGER checkpoint_ns
        BLOB parent_id
        BLOB checkpoint
        BLOB metadata
    }

    writes {
        TEXT thread_id
        INTEGER checkpoint_ns
        TEXT task_id
        INTEGER idx
        BLOB channel
        BLOB value
    }
```

- **`specialists`** — Registered specialist agents with cached agent cards
- **`tasks` / `travel_specialist_tasks` / `nutrition_specialist_tasks`** — A2A task lifecycle (one table per specialist to avoid conflicts)
- **`checkpoints` + `writes`** — LangGraph checkpoint state for all agents

---

## Docker Networking

```mermaid
graph LR
    subgraph "Host"
        Browser["Browser<br/>localhost:3000"]
    end

    subgraph "Docker Network"
        FE["frontend<br/>:80→:3000"]
        ORC["orchestrator<br/>:8000"]
        TS["travel-specialist<br/>:8001"]
        NS["nutrition-specialist<br/>:8002"]
    end

    Browser -->|localhost:3000| FE
    FE -->|localhost:8000| ORC
    ORC -->|travel-specialist:8001| TS
    ORC -.->|nutrition-specialist:8002| NS
```

The orchestrator uses **internal Docker hostnames** (`travel-specialist:8001`, `nutrition-specialist:8002`) to reach specialists. The frontend uses **localhost** ports to reach the orchestrator. The `SPECIALIST_URL_REMAPS` setting translates public localhost URLs (that the frontend registers) to internal Docker URLs (that the orchestrator uses for routing).
