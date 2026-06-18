# Nimbus Chat

> A multi-agent chat workspace powered by the **A2A (Agent-to-Agent) protocol**, LangChain `create_agent`, LangGraph checkpointing, and an elegant ChatGPT-style UI with Kimi-inspired streaming.

Nimbus Chat demonstrates an **orchestrator-specialist architecture** where a central orchestrator agent receives user messages, decides which specialist(s) to involve (using structured LLM routing), and either responds directly or **fans out to multiple specialists in parallel**, synthesizing their combined expertise into a single coherent response.

---

## ✨ Features

- **A2A Protocol Native** — Orchestrator and specialists communicate via the A2A JSON-RPC protocol with streaming SSE, agent cards, and task lifecycle management.
- **Parallel Fan-Out** — When a query spans multiple domains (e.g. travel + nutrition), the orchestrator calls all relevant specialists concurrently and synthesizes a unified response.
- **Specialist Agents** — Travel Planner and Nutritionist, each with domain-specific LangChain tools (Tavily web search), system prompts, and skills.
- **Conversation Continuity** — Threaded sessions with LangGraph SQLite checkpointing; the orchestrator remembers specialist-routed turns across direct follow-ups.
- **Streaming UI** — Real-time token streaming with an inline "thinking/activity" trail (Kimi-style), markdown rendering, and per-specialist attribution.
- **OpenRouter + Tavily** — LLM via OpenRouter (`init_chat_model`), web research via Tavily — both configurable through environment variables.

---

## 🏗️ Architecture

```mermaid
graph TB
    User["👤 User (Browser)"]
    FE["⚛️ Frontend<br/>React + @a2a-js/sdk<br/>:3000"]
    ORC["🧠 Orchestrator<br/>FastAPI + A2A Server<br/>:8000"]
    TS["✈️ Travel Specialist<br/>LangChain create_agent<br/>:8001"]
    NS["🥗 Nutrition Specialist<br/>LangChain create_agent<br/>:8002"]
    DB[("🗄️ SQLite<br/>/data/nimbus-chat.db")]
    Tavily["🔍 Tavily API"]
    LLM["🤖 OpenRouter LLM"]

    User -->|HTTP| FE
    FE -->|A2A JSON-RPC / SSE| ORC
    ORC -->|A2A JSON-RPC| TS
    ORC -.->|A2A JSON-RPC| NS
    ORC --> DB
    TS --> DB
    NS --> DB
    TS --> Tavily
    NS --> Tavily
    ORC --> LLM
    TS --> LLM
    NS --> LLM
```

### How it works

```mermaid
sequenceDiagram
    participant U as User (Browser)
    participant F as Frontend
    participant O as Orchestrator
    participant R as Router Agent
    participant S1 as Travel Specialist
    participant S2 as Nutrition Specialist
    participant SY as Synthesizer Agent

    U->>F: "Plan a healthy Tokyo trip"
    F->>O: A2A sendMessageStream(contextId)
    O->>R: Route decision (structured output)
    R-->>O: should_route=true, 2 specialists

    par Parallel fan-out
        O->>S1: A2A sendMessageStream
        S1-->>O: Streaming artifact (itinerary)
    and
        O->>S2: A2A sendMessageStream
        S2-->>O: Streaming artifact (meal plan)
    end

    O->>SY: Synthesize(specialist responses)
    SY-->>O: Streamed unified response
    O-->>F: Synthesized artifact (SSE stream)
    F-->>U: Render markdown + activity trail
```

### Routing decision flow

```mermaid
flowchart TD
    Start["📨 User message received"] --> Task["Create A2A Task<br/>(context_id = thread_id)"]
    Task --> Router["🧭 Router Agent<br/>create_agent + structured output<br/>returns RouteDecision"]
    Router --> Decide{"should_route?"}

    Decide -->|No| Direct["💬 Responder Agent<br/>streams direct response"]
    Decide -->|Yes, 1 specialist| Single["🔀 Route to 1 specialist<br/>stream specialist response directly"]
    Decide -->|Yes, 2+ specialists| FanOut["⚡ Parallel fan-out<br/>asyncio.gather all specialists"]

    FanOut --> Collect["📥 Collect all responses"]
    Collect --> Synth["🧬 Synthesizer Agent<br/>combines responses<br/>streams unified answer"]
    Single --> Record["💾 record_exchange<br/>into responder thread"]
    Synth --> Record
    Direct --> Done["✅ Task completed"]
    Record --> Done
```

---

## 🚀 Quick Start

### Prerequisites

- [Docker](https://docs.docker.com/get-docker/) + Docker Compose
- An [OpenRouter](https://openrouter.ai/) API key (for the LLM)
- A [Tavily](https://tavily.com/) API key (for web research, optional but recommended)

### 1. Clone & configure

```bash
git clone https://github.com/chandra447/nimbus-chat.git
cd nimbus-chat
cp .env.example .env
```

Edit `.env` and set:
```env
OPENROUTER_API_KEY=sk-or-v1-...       # Required — powers all LLM calls
TAVILY_API_KEY=tvly-...                # Recommended — web research for specialists
TAVILY_ENABLED=true                    # Enable Tavily tools
```

### 2. Launch

```bash
docker compose up --build
```

### 3. Register specialists

Open `http://localhost:3000`, click the **specialists badge** (top-right), and register:

| Specialist | URL |
|---|---|
| Nimbus Travel Planner | `http://localhost:8001` |
| Nimbus Nutritionist | `http://localhost:8002` |

### 4. Chat!

Try these prompts:

- **Travel:** *"Plan a 4-day Tokyo itinerary under $1,500"* → routes to Travel Planner
- **Nutrition:** *"Create a high-protein vegetarian meal plan for muscle gain"* → routes to Nutritionist
- **Cross-domain:** *"I'm traveling to Tokyo for 5 days. Plan my sightseeing AND a healthy high-protein meal plan"* → **parallel fan-out** to both specialists + synthesis

---

## 📁 Project Structure

```
nimbus-chat/
├── docker-compose.yml              # 4-service orchestration
├── .env.example                    # Environment template
│
├── frontend/                       # React + TypeScript + Vite
│   ├── src/
│   │   ├── routes/home-page.tsx    # Main chat UI (Kimi-style streaming)
│   │   ├── lib/a2a.ts              # @a2a-js/sdk client factory
│   │   ├── lib/a2a-helpers.ts      # A2A event parsing helpers
│   │   └── lib/orchestrator-api.ts # Specialist management REST calls
│   └── Dockerfile                  # Multi-stage: node build → nginx serve
│
├── backend/
│   ├── main.py                     # Orchestrator entrypoint (:8000)
│   ├── specialist_main.py          # Specialist entrypoint (:8001/:8002)
│   │
│   ├── app/
│   │   ├── settings.py             # Pydantic settings (env-driven)
│   │   ├── llm.py                  # init_chat_model("openrouter:...")
│   │   ├── checkpointing.py        # LangGraph AsyncSqliteSaver
│   │   │
│   │   ├── orchestrator/
│   │   │   ├── service.py          # FastAPI app + A2A routes + CORS
│   │   │   ├── executor.py         # OrchestratorExecutor (fan-out + synthesis)
│   │   │   ├── routing.py          # Router, Responder, Synthesizer agents
│   │   │   ├── registry.py         # Specialist registry (SQLite + agent-card fetch)
│   │   │   ├── middleware.py       # Specialist prompt injection middleware
│   │   │   ├── api.py              # REST API (register/refresh/list specialists)
│   │   │   ├── models.py           # Pydantic models for specialist records
│   │   │   └── agent_card.py       # Orchestrator's own A2A agent card
│   │   │
│   │   └── specialist/
│   │       ├── builder.py          # Generic specialist framework (config + executor + card)
│   │       ├── configs.py          # Travel + Nutrition specialist configs
│   │       ├── service.py          # FastAPI app factory for any specialist
│   │       └── agent_card.py       # (Legacy) travel agent card builder
│   │
│   └── Dockerfile                  # Python 3.13 + uv
│
└── docs/                           # Detailed documentation
    ├── architecture.md
    ├── a2a-protocol.md
    ├── specialists.md
    └── development.md
```

---

## 🔧 Configuration

### Environment Variables

| Variable | Default | Description |
|---|---|---|
| `OPENROUTER_API_KEY` | *(none)* | **Required.** API key for OpenRouter LLM. |
| `OPENAI_API_KEY` | *(none)* | Fallback OpenAI key (unused if OpenRouter set). |
| `OPENAI_MODEL` | `gpt-5.4-mini` | Fallback model name. |
| `TAVILY_ENABLED` | `true` | Enable Tavily web search tools. |
| `TAVILY_API_KEY` | *(none)* | Tavily API key for web research. |
| `SPECIALIST_TYPE` | `travel` | Which specialist config to run (`travel` / `nutrition`). |
| `SPECIALIST_PORT` | `8001` | Port the specialist listens on. |
| `SPECIALIST_PUBLIC_URL` | `http://localhost:8001` | URL the frontend uses to register. |
| `SPECIALIST_INTERNAL_URL` | `http://travel-specialist:8001` | Docker-internal URL for orchestrator→specialist. |
| `SPECIALIST_URL_REMAPS` | *(see compose)* | Maps public→internal URLs for multi-specialist routing. |
| `SPECIALIST_CARD_REFRESH_TTL_SECONDS` | `300` | Agent-card cache TTL. `0` = always refresh, `-1` = never. |
| `CORS_ORIGINS` | `*` | Comma-separated allowed origins. |
| `VITE_ORCHESTRATOR_BASE_URL` | `http://localhost:8000` | Frontend → orchestrator URL. |

### LLM Model

The backend uses `init_chat_model("openrouter:deepseek/deepseek-v4-flash")`. To change the model, edit `backend/app/llm.py`:

```python
chat_model = init_chat_model(model="openrouter:anthropic/claude-3.5-sonnet")
```

---

## 📚 How A2A Works in Nimbus Chat

The **A2A (Agent-to-Agent) protocol** defines how agents discover each other and exchange messages. Here's how Nimbus Chat uses it:

### Agent Discovery (Agent Cards)

Every A2A agent exposes a **card** at `/.well-known/agent-card.json` describing its name, skills, capabilities, and protocol endpoints:

```json
{
  "name": "Nimbus Travel Planner",
  "description": "A travel-planning specialist...",
  "capabilities": { "streaming": true },
  "supported_interfaces": [
    { "url": "http://travel-specialist:8001/a2a/jsonrpc", "protocol_binding": "JSONRPC" },
    { "url": "http://travel-specialist:8001/a2a", "protocol_binding": "HTTP+JSON" }
  ],
  "skills": [
    { "id": "itinerary_creation", "name": "Itinerary creation", "tags": ["travel"], "examples": [...] }
  ]
}
```

When a specialist is registered, the orchestrator fetches its card, persists it to SQLite, and injects the skills/examples into the router's system prompt via `RegisteredSpecialistPromptMiddleware`.

### Message Flow (JSON-RPC + SSE Streaming)

```mermaid
sequenceDiagram
    participant C as Client (Frontend)
    participant O as Orchestrator (A2A Server)

    C->>O: POST /a2a/jsonrpc<br/>method: "message/stream"<br/>body: { message, contextId }
    O-->>C: SSE stream opens

    O-->>C: event: task (TASK_STATE_SUBMITTED)
    O-->>C: event: status_update (TASK_STATE_WORKING)
    Note over O: Router decides routing
    O-->>C: event: status_update ("Routing to specialist...")
    O-->>C: event: artifact_update (first chunk, append=false)
    O-->>C: event: artifact_update (chunk, append=true)
    O-->>C: event: artifact_update (chunk, append=true)
    O-->>C: event: status_update (TASK_STATE_COMPLETED)
    O-->>C: SSE stream closes
```

The frontend uses `@a2a-js/sdk`'s `client.sendMessageStream(request)` to consume this SSE stream and render events as they arrive.

### Task Lifecycle

```mermaid
stateDiagram-v2
    [*] --> Submitted: Task created
    Submitted --> Working: Executor starts
    Working --> Working: Status updates (routing, specialist working)
    Working --> Completed: Response finished
    Working --> Failed: Error occurred
    Working --> Canceled: Client cancelled
    Completed --> [*]
    Failed --> [*]
    Canceled --> [*]
```

---

## 🧬 Conversation Continuity

Each conversation has a stable `contextId` (UUID) generated by the frontend. This ID flows through the entire stack:

```mermaid
graph LR
    FE["Frontend<br/>contextId = conversation.id"] -->|A2A message| ORC["Orchestrator<br/>thread_id = contextId"]
    ORC -->|Router| RT["LangGraph thread<br/>{contextId}:route"]
    ORC -->|Responder| RP["LangGraph thread<br/>{contextId}:respond"]
    ORC -->|Specialist A2A| SP["Specialist<br/>{contextId}:travel"]
    ORC -->|Synthesizer| SY["LangGraph thread<br/>{contextId}:synthesize"]
    ORC -->|record_exchange| RP

    RT --> CP[("SQLite Checkpoints")]
    RP --> CP
    SP --> CP
    SY --> CP
```

When a specialist responds, the orchestrator calls `responder.record_exchange()` to inject the user message + specialist response into the responder's LangGraph thread (via `aupdate_state` with `as_node='model'`). This ensures **cross-path continuity**: a direct follow-up after a specialist-routed turn still has full context.

---

## ⚡ Parallel Fan-Out

When the router selects multiple specialists:

```mermaid
graph LR
    Q["📨 User query<br/>(cross-domain)"] --> R["🧭 Router<br/>selects 2+ specialists"]
    R --> F["⚡ asyncio.gather"]
    F --> S1["Specialist A<br/>(concurrent)"]
    F --> S2["Specialist B<br/>(concurrent)"]
    S1 --> C["📥 Collect responses"]
    S2 --> C
    C --> SY["🧬 Synthesizer Agent<br/>combines + streams"]
    SY --> U["👤 Unified response"]
```

1. **Router** returns `RouteDecision` with `specialists: [SpecialistRoute, ...]`
2. **`_stream_parallel_specialist_response`** calls all specialists concurrently via `asyncio.gather`
3. Each specialist's full response is collected
4. **Synthesizer agent** (a `create_agent` with a synthesis-focused system prompt) takes all responses + the original query and streams a unified answer
5. The synthesized response is recorded in the responder's thread for future continuity

---

## ➕ Adding a New Specialist

1. **Add a config** in `backend/app/specialist/configs.py`:

```python
fitness_config = SpecialistConfig(
    name='Nimbus Fitness Coach',
    description='A fitness specialist for workout plans and exercise guidance.',
    system_prompt='You are Nimbus Fitness Coach...',
    tavily_tool_name='research_fitness',
    tavily_tool_description='Search for current fitness research and exercise science.',
    table_name_prefix='fitness_specialist',
    artifact_name='fitness-plan',
    skills=[
        SpecialistSkillSpec(
            id='workout_planning',
            name='Workout planning',
            description='Creates structured workout routines...',
            tags=['fitness', 'workout'],
            examples=['Create a 4-day hypertrophy split', 'Design a beginner home workout'],
        ),
    ],
)

SPECIALIST_CONFIGS['fitness'] = fitness_config
```

2. **Add a Docker Compose service**:

```yaml
fitness-specialist:
  build:
    context: ./backend
    dockerfile: Dockerfile
  command: ["uv", "run", "python", "specialist_main.py"]
  environment:
    SPECIALIST_TYPE: fitness
    SPECIALIST_PORT: "8003"
    SPECIALIST_PUBLIC_URL: http://localhost:8003
    SPECIALIST_INTERNAL_URL: http://fitness-specialist:8003
    # ... (same as other specialists)
  ports:
    - "8003:8003"
```

3. **Update `SPECIALIST_URL_REMAPS`** in the orchestrator service:

```yaml
SPECIALIST_URL_REMAPS: http://localhost:8001=http://travel-specialist:8001,http://localhost:8002=http://nutrition-specialist:8002,http://localhost:8003=http://fitness-specialist:8003
```

4. **Register** the specialist via the UI at `http://localhost:8003`.

That's it — the router will automatically consider the new specialist for relevant queries.

---

## 📖 Documentation

- [Architecture Deep Dive](docs/architecture.md) — Component breakdown, data flow, and design decisions
- [A2A Protocol Guide](docs/a2a-protocol.md) — How A2A discovery, messaging, and streaming work
- [Specialists Guide](docs/specialists.md) — Building, configuring, and registering specialist agents
- [Development Guide](docs/development.md) — Local development, testing, and debugging

---

## 🛠️ Tech Stack

| Layer | Technology |
|---|---|
| **Frontend** | React 19, TypeScript, Vite, Tailwind CSS v4, Framer Motion, react-markdown |
| **A2A SDK (JS)** | `@a2a-js/sdk` — agent card resolution, streaming message client |
| **Backend** | Python 3.13, FastAPI, Pydantic v2, uvicorn |
| **A2A SDK (Python)** | `a2a-sdk` — server routes, task store, event queues, client |
| **LLM Framework** | LangChain `create_agent`, LangGraph (state, checkpointing, streaming) |
| **LLM Provider** | OpenRouter via `init_chat_model("openrouter:...")` |
| **Web Research** | Tavily API (LangChain `@tool` integration) |
| **Persistence** | SQLite (shared Docker volume) — A2A tasks + LangGraph checkpoints + specialist registry |
| **Deployment** | Docker Compose (4 services: orchestrator, travel, nutrition, frontend) |

---

## 📄 License

MIT
