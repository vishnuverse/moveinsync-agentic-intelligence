#!/bin/sh
# docker-compose `seed` service entrypoint (plan §9): applies the schema in
# order, then runs the synthetic data generator. Idempotent enough for a demo
# restart -- schema.sql/triggers.sql use CREATE TABLE/CREATE OR REPLACE
# (triggers.sql also DROPs its own triggers first), api_schema.sql uses
# CREATE TABLE IF NOT EXISTS, and generate.py TRUNCATEs its own tables before
# reseeding (see backend/db/README.md) -- so re-running this container on an
# already-seeded volume is safe, not just safe on a fresh one.
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

echo "seed: generating synthetic data"
python db/seed/generate.py

# Real-data ingestion (backend/config/data_contract.yaml, the active
# default, points at mis.* -- see backend/db/real_data/README.md). Only
# runs when the real CSVs are actually present (DATA_DIR, mounted from the
# host's gitignored data/ folder -- see docker-compose.yml) since that
# ~550MB dataset isn't part of the image/repo and a fresh clone without it
# should still boot cleanly on synthetic data alone (data_contract.yaml
# would then point at empty mis.* tables -- flip DATA_CONTRACT_PATH to
# data_contract.synthetic.yaml in that case, per this seed job's own
# skip-message below).
if [ -n "${DATA_DIR:-}" ] && [ -f "${DATA_DIR}/emp_Data.csv" ]; then
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
