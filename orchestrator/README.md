# Orchestraty — Orchestration Engine

The control plane of the Orchestraty AI Operations Platform. It turns a declared
**intent** into a graph of tasks, dispatches them to registered **agents**, tracks
their state, and reconciles reality to the goal — the "AI Kubernetes" model.

Built incrementally across Phase 1:

| Feature | Adds |
|---|---|
| scaffold *(this)* | FastAPI service, Postgres + Redis, `/health` |
| state-store | workflow / task / agent / event tables |
| task-engine | task state machine + DAG + retries |
| event-bus | Redis Streams dispatch |
| agent-registry | agent registration + routing |
| intent-compiler | intent -> task DAG |
| orchestrator-api | submit / query / stream |
| reference-agent | end-to-end heartbeat demo |

## Run locally

From the repo root:

    docker compose up --build      # or:  ./dev.ps1

- API:    http://localhost:8000
- Health: http://localhost:8000/health
- Docs:   http://localhost:8000/docs

## Configuration

Every setting is overridable via an `ORCH_*` environment variable or a `.env`
file at the repo root (see `.env.example`).
