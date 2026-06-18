# Plan: Nimbus Chat frontend + A2A backend

## Context
- Build a React frontend (React Router + shadcn/ui + Framer Motion) for a chat app that always talks to a Python orchestrator A2A server.
- Users manually onboard/register specialist agents with the orchestrator via a form.
- The orchestrator routes user requests to an appropriate specialist based on the specialist's agent card / skills.
- The frontend must show both staged events and token-by-token streaming.
- Backend should use `uv`, `pydantic-settings`, OpenAI-compatible provider config, and model `gpt-5.4-mini`.
- Deployment target is Docker-based: run **3 services** via Docker Compose using container images:
  - `frontend`
  - `orchestrator`
  - `travel-specialist`
- Existing repo state:
  - root repo currently only contains `PLAN.md`
  - `backend/` exists with `pyproject.toml`, `main.py`, `.venv`, and A2A/FastAPI/LangGraph dependencies already installed
  - `backend/main.py` is still a placeholder

## Approach
- Keep the frontend talking only to the orchestrator.
- Build two Python A2A services with FastAPI + A2A Python SDK:
  - orchestrator server: chat entrypoint, specialist registry, routing, persistence, and upstream streaming to the frontend
  - specialist server: domain-specific executor registered with the orchestrator
- Build a sibling `frontend/` React app that uses the A2A JS client to talk to the orchestrator.
- Use LangGraph for orchestration / specialist workflows and `pydantic-settings` for environment-driven configuration.
- Use SQLite-backed persistence for workflow checkpointing, and use A2A task persistence compatible with SQLite as needed.
- Recommended specialist use case: **travel planner specialist**. This makes routing easy to demo from agent-card skills/examples (e.g. itinerary generation, budget-aware recommendations, destination planning).
- Tavily can be added as an optional travel research/search tool for the specialist if live travel lookup is needed for MVP; its config should also come from `pydantic-settings`.
- Use the A2A Python SDK's REST streaming endpoint (`POST /message:stream`), which emits SSE frames.
- Use the **latest alpha A2A JS SDK** on the frontend so the React client can talk A2A directly to the orchestrator. The intended client pattern is `ClientFactory().createFromUrl(baseUrl)` plus `client.sendMessageStream(...)`, consuming `task`, `status-update`, and `artifact-update` events with `for await ... of`.
- Frontend staged UX should map A2A events directly into UI state:
  - `task` -> chat run created / active specialist context
  - `status-update` -> staged orchestration lifecycle updates
  - `artifact-update` -> incremental token/content rendering

## Files to modify
- `backend/pyproject.toml`
- `backend/main.py`
- New backend app modules for settings, agent cards, executors, routing, persistence, and specialist registration
- New frontend app scaffold in `frontend/`
- Docker assets at repo root and/or per service (`docker-compose.yml`, Dockerfiles, container ignore files)

## Reuse
- Existing backend dependencies already present in `backend/pyproject.toml`:
  - `a2a-sdk[fastapi]`
  - `fastapi`
  - `langchain`
  - `langchain-openai`
  - `langgraph`
  - `pydantic-settings`
  - `uvloop`
- A2A Python SDK pieces confirmed from installed package/source:
  - `a2a.client.create_client(...)` can create a client from an agent URL or `AgentCard`
  - `A2ACardResolver` fetches the public agent card from `/.well-known/agent-card.json`
  - FastAPI integration is done by mounting `create_agent_card_routes(...)`, `create_jsonrpc_routes(...)`, `create_rest_routes(...)`, then `add_a2a_routes_to_fastapi(...)`
  - REST streaming route is `POST /message:stream`
  - `TaskUpdater` supports task status events plus artifact chunk updates (`append`, `last_chunk`) which can model staged + token streaming
  - `RequestContext.get_user_input()` extracts the user text from the incoming A2A message
- Frontend A2A usage confirmed by the JS SDK pattern the user provided:
  - `ClientFactory` can create a client from the orchestrator base URL
  - `sendMessageStream(...)` yields stream events that can be consumed directly in the browser/client app
- Gaps identified:
  - SQLite-backed A2A task stores require SQL extras (`a2a-sdk[sqlite]` / SQLAlchemy), which are not currently installed through `backend/pyproject.toml`
  - LangGraph SQLite checkpointing likely needs `langgraph-checkpoint-sqlite`, which is not currently present in the backend environment
  - Tavily dependency/env vars are not yet present and should only be added if we decide to enable live search in the travel specialist
  - No frontend app directory or Docker Compose assets exist yet in the repo

## Steps
- [x] Keep the monorepo as `backend/` + `frontend/`.
- [x] Add frontend app with React Router + shadcn/ui + Framer Motion and latest alpha `@a2a-js/sdk` client integration.
- [x] Add missing SQLite-related dependencies for both A2A persistence and LangGraph checkpointing, using `uv` for Python dependency management.
- [x] Define backend settings via `pydantic-settings` for OpenAI-compatible base URL, API key, model, SQLite path, orchestrator/specialist URLs, and optional Tavily config.
- [x] Implement orchestrator agent card, specialist registry schema, and manual registration endpoint/form contract.
- [x] Implement orchestrator `AgentExecutor` that receives all chat requests, emits staged events, resolves/loads registered specialists, creates A2A clients to specialists, and re-streams specialist output to the frontend.
- [x] Implement travel planner specialist agent card with routing-friendly skills/examples (e.g. destination planning, itinerary creation, budget travel advice, activity recommendations) and build its `AgentExecutor`.
- [x] Optionally integrate Tavily into the travel specialist workflow for live travel research.
- [x] Build React chat UI with message list, staged event timeline, token streaming renderer, and specialist onboarding form.
- [x] Add Dockerfiles and `docker-compose.yml` for the 3 runtime services: frontend, orchestrator, and travel specialist.
- [ ] Verify end-to-end A2A registration, routing, streaming, Dockerized startup, and checkpoint persistence.

## Verification
- Frontend can create an A2A JS client for the orchestrator, submit a message with `sendMessageStream(...)`, and render:
  - route/status events (e.g. registered specialist selected, orchestrator routing, specialist running)
  - incremental token/artifact updates during generation
  - final answer completion state
- Registering a specialist through the frontend persists enough metadata for the orchestrator to reuse it later.
- The orchestrator can fetch/validate a specialist agent card and create an A2A client for it.
- The travel specialist agent card exposes appropriate skills/examples so the orchestrator can route travel requests reliably.
- A routed request reaches the specialist and the specialist response streams back through the orchestrator to the frontend.
- If Tavily is enabled, travel queries that require fresh lookup can use it successfully without breaking the streaming flow.
- SQLite checkpoint/task state survives process restart for the selected persisted components.
- `docker compose up` can bring up frontend, orchestrator, and travel specialist together with working cross-service URLs.
- Manual smoke tests plus backend/frontend tests to be defined once repo layout is implemented.
