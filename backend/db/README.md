# backend/db

App/infra/reference Postgres schema + reference seed data (plan §7/§8). The
real business data lives in the `mis` schema (see `real_data/`); this schema
holds only the non-business tables `backend/config/data_contract.yaml` and the
app depend on: `sustainability_targets`, `data_quality_flags`,
`agent_notifications`, `agent_reports`.

## Local setup

Requires a Postgres instance with the `pgvector` extension available (the
`pgvector/pgvector:pg16` Docker image has it preinstalled). The docker-compose
`postgres` service (owned by another part of this build) should already
satisfy this; for standalone use:

```bash
docker run -d --name moveinsync-postgres \
  -e POSTGRES_USER=moveinsync -e POSTGRES_PASSWORD=moveinsync -e POSTGRES_DB=moveinsync \
  -p 5432:5432 pgvector/pgvector:pg16
```

Apply the schema:

```bash
psql "postgresql://moveinsync:moveinsync@localhost:5432/moveinsync" -f backend/db/schema.sql
```

Seed reference data — sustainability targets (Python 3.11+, `psycopg[binary]` installed):

```bash
DATABASE_URL="postgresql://moveinsync:moveinsync@localhost:5432/moveinsync" \
  python backend/db/seed/generate.py
```

The seed script `TRUNCATE ... RESTART IDENTITY CASCADE`s the data tables
first, so it's safe to re-run after a schema change.

## What gets seeded

~6 teams / 36-48 employees, 5 vendors, 15 routes, 15 drivers, several hundred
`route_trips` over the last 60 days with proportionate costs/incidents/
emissions/commute/attendance rows, the 3 `sustainability_targets` benchmark
rows from plan §8, and a handful of curated `sql_agent_examples`. It also
seeds deliberately messy rows (nulls, duplicate-looking trips, implausible
timestamps) logged into `data_quality_flags`, plus three unmistakable demo
anomalies -- printed at the end of the run:

- one route with a sustained sharp delay spike over the last ~25 days
- one vendor with a clear cost/SLA divergence vs. every other vendor
- one route with an emissions trend rising well above the 82 gCO2/passenger-km
  ICE baseline

## Migrations

`backend/db/migrations/` is currently empty (nothing has needed a migration
yet) -- `schema.sql` is the single source of truth for now.
