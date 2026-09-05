# MoveInSync-AIR — Agentic Intelligence & Reporting Layer

**Team: Sudo**

An agentic layer for enterprise employee mobility that **senses** operational
events as they happen, **reasons** about their business impact, and **acts**
— autonomously where safe, with human sign-off where it isn't. Built for the
MoveInSync hackathon problem statement (see
[`docs/PROBLEM_STATEMENT.md`](docs/PROBLEM_STATEMENT.md) for the original
brief this repo is scored against).

**Live demo:** https://app.inferencezero.com — running against the same
anonymised sample dataset as local development, no live third-party system
access.

For the full technical picture (real architecture diagram, per-component
detail, data flow, cost/scale notes), see
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md). This README covers what the
system is and how to run it.

---

## What it does

Three personas share one backend and one dataset, re-scoped by role — not
three separate apps:

- **Transport Manager** — live safety/delay alerts, escort-compliance
  monitoring, same-day operational view.
- **Line Manager** — commute-attendance correlation for their team, isolating
  transport-caused delay from employee no-shows.
- **Transport & Facilities Head** — billing-slab discrepancy auditing,
  sustainability/EV-transition tracking, leadership-ready reports.

A natural-language chat interface answers ad-hoc questions across all of the
above (grounded in a real generated-SQL trail, not a canned response), and a
live event feed shows the sense→reason→act loop reacting to real data in
real time.

Every metric card carries a `context_note` — at least one reference point
(historical trend, SLA/goal, or peer/vendor attribution), not a bare number.

## Architecture, in one sentence

`Postgres LISTEN/NOTIFY` → **sense** (9 anomaly/compliance detectors) →
**reason** (LangGraph subgraph: SQL agent + research agent + impact context +
root-cause synthesis) → **act** (LangGraph subgraph with a real
`interrupt()` human-approval gate) → Redis pub/sub → WebSocket/SSE → the
React dashboard, live, with no polling. Full diagram and per-component
breakdown: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## Tech stack

FastAPI (Python) + LangGraph + LangMem · PostgreSQL · Redis · React/Vite/TypeScript
· Docker Compose · Cloudflare Tunnel for public exposure. See
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md#technology-stack) for the full
table and why each piece is there.

## Running it

### Prerequisites
- Docker + Docker Compose

### Quick start (synthetic reference data only)

```bash
docker compose up --build
```

This boots Postgres, Redis, the backend API, the scheduler (sense/reason/act
autonomy loop), the frontend, and a Cloudflare Tunnel. The app works out of
the box without the real dataset — see
[`docker-compose.yml`](docker-compose.yml) for the local ports and tunnel
config.

### Running against the real (anonymised) dataset

The dashboards, sense detectors, and demo scenarios are built for the real
MoveInSync sample dataset, not the synthetic placeholder — use this for an
actual demo.

1. Place the dataset CSVs in `data/` at this repo's root (gitignored — they
   stay local, never committed):
   ```
   data/
   ├── Ride_data _trip-may_2026.csv
   ├── Ride_data _trip-June_2026.csv
   ├── Ride_data _trip-July_2026.csv
   ├── emp_Data.csv
   ├── bill_data.csv
   ├── alerts_data.csv
   └── trip_feedback.csv
   ```
   Field-level documentation for each file lives in
   [`data/Dictionary/`](data/Dictionary/) once placed (see
   [`backend/db/real_data/README.md`](backend/db/real_data/README.md) for the
   ingestion pipeline itself).
2. Run `docker compose up --build`. The `seed` service ingests the real data
   automatically the first time it finds these files against an empty
   database. On every later `docker compose up` it skips re-ingesting (data's
   already there) — to force a full re-ingest, drop the Postgres volume first:
   `docker compose down -v`.
3. Optional: to see the live sense→reason→act loop react to a fresh event
   instead of only the historically-seeded state, replay a real trip with
   today's timestamp:
   ```bash
   DATABASE_URL=$DATABASE_URL python backend/db/real_data/replay.py --scenario delay_spike --count 5 --interval-seconds 3
   ```
   See [`docs/DEMO_RUNBOOK.md`](docs/DEMO_RUNBOOK.md) for the full walkthrough
   (including the interrupt/approval flow) mapped to each judging criterion.

### Local development (without Docker)

- **Backend:** `cd backend && pip install -r requirements.txt`, then run the
  FastAPI app (`app/main.py`) and scheduler (`app/schedulers/main.py`)
  against a local Postgres + Redis. Copy `backend/.env.example` to
  `backend/.env` and fill in `DATABASE_URL`, `REDIS_URL`, and an LLM provider
  key.
- **Frontend:** `cd frontend && npm install && npm run dev` (Vite dev server;
  `npm run build` for a production build). Copy `frontend/.env.example` to
  `frontend/.env` to point it at a running backend.
- **Tests:** `backend/tests/golden_qa.py` is the golden Q&A regression set for
  the chat/SQL-agent path.

## Project structure

```
moveinsync-agentic-intelligence/
├── backend/
│   ├── app/
│   │   ├── graph/           # sense/, reason/, act/ — the LangGraph pipeline
│   │   ├── api/              # FastAPI routes (dashboard, chat, ws, sse, ...)
│   │   ├── memory/           # LangMem episodic/semantic/procedural stores
│   │   ├── llm/               # LLM provider + Redis-backed cost circuit breaker
│   │   ├── schedulers/       # interval poll + LISTEN/NOTIFY bridge
│   │   ├── services/         # dashboard/report/notification query layers
│   │   └── contracts/        # data_contract.yaml loader
│   ├── config/data_contract.yaml   # logical→physical schema mapping (retarget DB here)
│   ├── db/                    # schema, migrations, real_data ingestion + seed
│   └── tests/
├── frontend/
│   └── src/
│       ├── dashboards/       # per-persona dashboards
│       ├── pages/            # Live, Chat, Notifications, Activity, Outbox
│       ├── components/       # LiveEventFeed, charts, shared UI
│       └── api/               # REST/WS/SSE clients
├── docs/                      # architecture, PRD, backlog, demo runbook
├── cloudflared/               # tunnel routing config
└── docker-compose.yml
```

## Documentation map

- [`docs/PROBLEM_STATEMENT.md`](docs/PROBLEM_STATEMENT.md) — the original
  hackathon brief this system is built and scored against.
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — real architecture diagram,
  component detail, data flow, cost/scale notes.
- [`docs/moveinsync-prd-v3.md`](docs/moveinsync-prd-v3.md) — the detailed
  internal feature-by-feature PRD (persona → sense → reason → act mapping,
  measurement protocols, expected outcomes).
- [`docs/DEMO_RUNBOOK.md`](docs/DEMO_RUNBOOK.md) — live-demo walkthrough
  mapped to each official judging criterion.
- [`docs/BACKLOG.md`](docs/BACKLOG.md) — actively-maintained list of what's
  built, designed-but-not-built, and explicitly out of scope. The
  source of truth for current gaps — check here before assuming something
  is or isn't done.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

MIT — see [LICENSE](LICENSE).
