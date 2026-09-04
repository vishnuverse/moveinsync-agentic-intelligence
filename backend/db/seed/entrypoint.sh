#!/bin/sh
# docker-compose `seed` service entrypoint (plan §9): applies the schema,
# then seeds synthetic data and (if present) ingests the real dataset --
# each stage gated to run exactly once per postgres volume, not on every
# `docker compose up`. First run on a fresh volume: full schema + synthetic
# seed + real-data ingest (if data/ has CSVs). Every later run: each stage
# checks its own tables and skips if already populated, so a plain restart
# comes back up in seconds instead of re-TRUNCATEing synthetic data and
# re-running the ~550MB real-data ingest ("the slow part") from scratch.
# Dropping real CSVs into data/ *after* the first boot still gets picked up
# on the next `docker compose up`, since that stage's gate is independent.
# To force a full re-seed of everything, wipe the volume: `docker compose
# down -v`.
set -eu

echo "seed: waiting for postgres at $DATABASE_URL"
until psql "$DATABASE_URL" -c '\q' 2>/dev/null; do
    sleep 1
done

# schema.sql's CREATE TABLE statements aren't idempotent (no IF NOT EXISTS --
# not this script's file to edit), so a second `docker compose up` on an
# already-seeded volume would otherwise fail this job on "relation already
# exists" and, via `depends_on: service_completed_successfully`, permanently
# block backend/scheduler from starting on every restart after the first.
# Guard on whether `teams` (schema.sql's first table) already exists instead.
SCHEMA_APPLIED=$(psql "$DATABASE_URL" -tAc "SELECT to_regclass('public.teams') IS NOT NULL")
if [ "$SCHEMA_APPLIED" = "t" ]; then
    echo "seed: schema already applied, skipping schema.sql/triggers.sql"
else
    echo "seed: applying schema.sql"
    psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f db/schema.sql

    echo "seed: applying triggers.sql"
    psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f db/triggers.sql
fi

# api_schema.sql is additive-only (CREATE TABLE IF NOT EXISTS) -- always safe
# to (re-)apply, including on an already-seeded volume that predates it.
echo "seed: applying api_schema.sql"
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f db/api_schema.sql

# generate.py TRUNCATEs+reinserts its own tables every call (see
# backend/db/README.md), which is safe but throws away anything a demo user
# did in the app and burns time on every restart -- so only run it the first
# time `teams` (its first INSERT target) is actually empty.
SYNTHETIC_SEEDED=$(psql "$DATABASE_URL" -tAc "SELECT EXISTS (SELECT 1 FROM teams)")
if [ "$SYNTHETIC_SEEDED" = "t" ]; then
    echo "seed: synthetic data already present (teams is non-empty), skipping generate.py"
else
    echo "seed: generating synthetic data"
    python db/seed/generate.py
fi

# Real-data ingestion (backend/config/data_contract.yaml, the active
# default, points at mis.* -- see backend/db/real_data/README.md). Only
# runs when the real CSVs are actually present (DATA_DIR, mounted from the
# host's gitignored data/ folder -- see docker-compose.yml) since that
# ~550MB dataset isn't part of the image/repo and a fresh clone without it
# should still boot cleanly on synthetic data alone (data_contract.yaml
# would then point at empty mis.* tables -- flip DATA_CONTRACT_PATH to
# data_contract.synthetic.yaml in that case, per this seed job's own
# skip-message below). Gated on `mis.trip` (ingest.py's fact table) already
# having rows, independent of the synthetic gate above, so this stage alone
# re-runs if CSVs show up in a later `docker compose up`.
REAL_DATA_INGESTED=$(psql "$DATABASE_URL" -tAc "SELECT CASE WHEN to_regclass('mis.trip') IS NULL THEN false ELSE EXISTS (SELECT 1 FROM mis.trip) END")
if [ "$REAL_DATA_INGESTED" = "t" ]; then
    echo "seed: real data already ingested (mis.trip is non-empty), skipping ingest.py"
elif [ -n "${DATA_DIR:-}" ] && [ -f "${DATA_DIR}/emp_Data.csv" ]; then
    echo "seed: real data found at $DATA_DIR, running real-data ingestion"
    python db/real_data/ingest.py

    echo "seed: applying escort-compliance/ack-time migration (post-ingest, mis.* was just rebuilt)"
    psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f db/migrations/001_add_escort_and_ack_time.sql

    echo "seed: applying mis.* NOTIFY triggers (post-ingest, mis.* was just rebuilt)"
    psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f db/real_data/triggers.sql
else
    echo "seed: no real data at DATA_DIR=${DATA_DIR:-<unset>} -- skipping real-data ingestion." \
         "backend/config/data_contract.yaml still points at mis.* (now empty)." \
         "Set DATA_CONTRACT_PATH=backend/config/data_contract.synthetic.yaml on backend/scheduler to run on synthetic data instead."
fi

echo "seed: done"
