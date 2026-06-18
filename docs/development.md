# Development Guide

## Prerequisites

- Python 3.13+ with [`uv`](https://docs.astral.sh/uv/)
- Node.js 22+ with npm
- Docker + Docker Compose
- An OpenRouter API key
- A Tavily API key (optional but recommended)

---

## Local Development (without Docker)

### Backend

```bash
cd backend
uv sync

# Set environment variables
export OPENROUTER_API_KEY=sk-or-v1-...
export TAVILY_API_KEY=tvly-...
export TAVILY_ENABLED=true

# Run orchestrator (terminal 1)
uv run python main.py

# Run travel specialist (terminal 2)
SPECIALIST_TYPE=travel SPECIALIST_PORT=8001 \
  SPECIALIST_PUBLIC_URL=http://localhost:8001 \
  SPECIALIST_INTERNAL_URL=http://localhost:8001 \
  uv run python specialist_main.py

# Run nutrition specialist (terminal 3)
SPECIALIST_TYPE=nutrition SPECIALIST_PORT=8002 \
  SPECIALIST_PUBLIC_URL=http://localhost:8002 \
  SPECIALIST_INTERNAL_URL=http://localhost:8002 \
  uv run python specialist_main.py
```

### Frontend

```bash
cd frontend
npm install
npm run dev
```

The Vite dev server runs at `http://localhost:5173` and proxies to the orchestrator at `http://localhost:8000`.

---

## Docker Development

### Rebuild after code changes

```bash
# Rebuild all services
docker compose up -d --build

# Rebuild just the orchestrator
docker compose up -d --build orchestrator

# Rebuild just the frontend
docker compose up -d --build frontend
```

### View logs

```bash
# All services
docker compose logs -f

# Specific service
docker compose logs -f orchestrator
docker compose logs -f travel-specialist
docker compose logs -f nutrition-specialist
```

### Reset everything

```bash
docker compose down -v   # -v removes the SQLite volume
docker compose up --build
```

---

## Debugging

### Check service health

```bash
curl http://localhost:8000/health  # Orchestrator
curl http://localhost:8001/health  # Travel specialist
curl http://localhost:8002/health  # Nutrition specialist
```

### Inspect agent cards

```bash
curl http://localhost:8000/.well-known/agent-card.json | jq
curl http://localhost:8001/.well-known/agent-card.json | jq
curl http://localhost:8002/.well-known/agent-card.json | jq
```

### Inspect SQLite

```bash
docker exec nimbus-chat-orchestrator-1 /app/.venv/bin/python -c "
import aiosqlite, asyncio
async def main():
    async with aiosqlite.connect('/data/nimbus-chat.db') as db:
        c = await db.execute(\"SELECT name FROM sqlite_master WHERE type='table'\")
        print('Tables:', [r[0] for r in await c.fetchall()])
        c = await db.execute('SELECT id, name, url FROM specialists')
        print('Specialists:')
        for row in await c.fetchall():
            print(' ', row)
        c = await db.execute('SELECT COUNT(*) FROM tasks')
        print('Tasks:', (await c.fetchone())[0])
asyncio.run(main())
"
```

### Test A2A streaming directly

```python
import asyncio
import httpx
from a2a.client import create_client, ClientConfig
from a2a.types.a2a_pb2 import Message, Part, Role, SendMessageRequest

async def main():
    config = ClientConfig(httpx_client=httpx.AsyncClient(timeout=300.0))
    client = await create_client('http://localhost:8000', client_config=config)
    req = SendMessageRequest(
        message=Message(
            message_id='test',
            role=Role.ROLE_USER,
            context_id='test-ctx',
            parts=[Part(text='Plan a 3-day Paris trip')],
        )
    )
    async for event in client.send_message(req):
        print(event)

asyncio.run(main())
```

### Register specialists via API

```bash
# Travel
curl -X POST http://localhost:8000/api/orchestrator/specialists \
  -H 'Content-Type: application/json' \
  -d '{"name":"Nimbus Travel Planner","url":"http://localhost:8001","description":"Travel","tags":["travel"],"notes":""}'

# Nutrition
curl -X POST http://localhost:8000/api/orchestrator/specialists \
  -H 'Content-Type: application/json' \
  -d '{"name":"Nimbus Nutritionist","url":"http://localhost:8002","description":"Nutrition","tags":["nutrition"],"notes":""}'
```

### List registered specialists

```bash
curl http://localhost:8000/api/orchestrator/specialists | jq
```

---

## Changing the LLM Model

Edit `backend/app/llm.py`:

```python
from langchain.chat_models import init_chat_model

def build_chat_model(settings, *, streaming=True):
    # Change the model string to use a different provider/model
    chat_model = init_chat_model(
        model="openrouter:anthropic/claude-3.5-sonnet",
        # model="openrouter:openai/gpt-4o",
        # model="openrouter:meta-llama/llama-3.1-70b-instruct",
        streaming=streaming,
    )
    return chat_model
```

Any model supported by OpenRouter works — see [openrouter.ai/models](https://openrouter.ai/models).

---

## Key Files to Know

| File | What it does |
|---|---|
| `backend/app/llm.py` | LLM model configuration |
| `backend/app/settings.py` | All environment-driven settings |
| `backend/app/orchestrator/routing.py` | Router, Responder, Synthesizer agents |
| `backend/app/orchestrator/executor.py` | Fan-out logic and streaming |
| `backend/app/orchestrator/registry.py` | Specialist registration + URL normalization |
| `backend/app/specialist/builder.py` | Generic specialist framework |
| `backend/app/specialist/configs.py` | Travel + Nutrition specialist configs |
| `frontend/src/routes/home-page.tsx` | Main chat UI |
| `frontend/src/lib/a2a.ts` | A2A client factory |
| `docker-compose.yml` | Service orchestration |

---

## Common Issues

### "ModuleNotFoundError: No module named 'langchain_openrouter'"

The `langchain-openrouter` package is required for OpenRouter integration. It's in `pyproject.toml`. If running locally:

```bash
cd backend
uv sync
```

### CORS errors in the browser

Ensure `CORS_ORIGINS` is set. For development, use `*`:

```env
CORS_ORIGINS=*
```

### Specialist not reachable by orchestrator

Check `SPECIALIST_URL_REMAPS` in the orchestrator's environment. The orchestrator needs internal Docker URLs to reach specialists:

```yaml
SPECIALIST_URL_REMAPS: http://localhost:8001=http://travel-specialist:8001,http://localhost:8002=http://nutrition-specialist:8002
```

### Recursion limit errors

LangGraph has a default recursion limit. If specialists with tools hit the limit, increase it in the executor:

```python
config={
    'configurable': {'thread_id': ...},
    'recursion_limit': 25,  # Increase from 15
}
```

### SAWarnings about declarative base

These warnings from the A2A SDK are harmless:

```
SAWarning: This declarative base already contains a class with the same class name...
```

They occur because multiple task stores share the same SQLAlchemy declarative base. Functionality is not affected.
