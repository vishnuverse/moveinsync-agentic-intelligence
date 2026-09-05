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
# Guard on whether `sustainability_targets` (a schema.sql table) already exists.
# NOTE: db/triggers.sql is gone -- it only ever defined NOTIFY triggers on the
# retired synthetic business tables; the real-data event path uses
# db/real_data/triggers.sql (applied post-ingest below).
SCHEMA_APPLIED=$(psql "$DATABASE_URL" -tAc "SELECT to_regclass('public.sustainability_targets') IS NOT NULL")
if [ "$SCHEMA_APPLIED" = "t" ]; then
    echo "seed: schema already applied, skipping schema.sql"
else
    echo "seed: applying schema.sql"
    psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f db/schema.sql
fi

# api_schema.sql is additive-only (CREATE TABLE IF NOT EXISTS) -- always safe
# to (re-)apply, including on an already-seeded volume that predates it.
echo "seed: applying api_schema.sql"
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f db/api_schema.sql

# generate.py now only (re)seeds the reference benchmarks in
# sustainability_targets (see backend/db/README.md) -- there is no synthetic
# business data any more. Run it once, the first time that table is empty.
REFERENCE_SEEDED=$(psql "$DATABASE_URL" -tAc "SELECT EXISTS (SELECT 1 FROM sustainability_targets)")
if [ "$REFERENCE_SEEDED" = "t" ]; then
    echo "seed: reference data already present (sustainability_targets non-empty), skipping generate.py"
else
    echo "seed: seeding reference data (sustainability_targets)"
    python db/seed/generate.py
fi

# Real-data ingestion (backend/config/data_contract.yaml, the active
# default, points at mis.* -- see backend/db/real_data/README.md). Only
# runs when the real CSVs are actually present (DATA_DIR, mounted from the
# host's gitignored data/ folder -- see docker-compose.yml) since that
# ~550MB dataset isn't part of the image/repo. A fresh clone without it still
# boots cleanly, but data_contract.yaml then points at empty mis.* tables
# (this project runs on the real dataset only -- add the CSVs to data/ and
# re-run to populate). Gated on `mis.trip` (ingest.py's fact table) already
# having rows, independent of the reference-seed gate above, so this stage
# alone re-runs if CSVs show up in a later `docker compose up`.
# Two-step check: `mis.trip` may not exist yet on a fresh volume, and Postgres
# resolves table references at PARSE time -- so a single query that names
# `mis.trip` errors out ("relation mis.trip does not exist") before any CASE
# can guard it, which under `set -e` aborts the whole seed before ingest runs.
# Check existence first (to_regclass never parse-fails), then non-emptiness.
if [ "$(psql "$DATABASE_URL" -tAc "SELECT to_regclass('mis.trip') IS NOT NULL")" = "t" ]; then
    REAL_DATA_INGESTED=$(psql "$DATABASE_URL" -tAc "SELECT EXISTS (SELECT 1 FROM mis.trip)")
else
    REAL_DATA_INGESTED="f"
fi
if [ "$REAL_DATA_INGESTED" = "t" ]; then
    echo "seed: real data already ingested (mis.trip is non-empty), skipping ingest.py"
elif [ -n "${DATA_DIR:-}" ] && [ -f "${DATA_DIR}/emp_Data.csv" ]; then
    echo "seed: real data found at $DATA_DIR, running real-data ingestion"
    python db/real_data/ingest.py

    echo "seed: applying mis.* NOTIFY triggers (post-ingest, mis.* was just rebuilt)"
    psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f db/real_data/triggers.sql
    # (mis.* column migrations 001/006 are applied by the generic migration
    # runner below -- which runs on every boot, so existing volumes that just
    # pulled newer code get them too, not only a fresh post-ingest boot.)
else
    echo "seed: no real data at DATA_DIR=${DATA_DIR:-<unset>} -- skipping real-data ingestion." \
         "backend/config/data_contract.yaml points at mis.* (now empty) -- drop the dataset CSVs into data/" \
         "and re-run 'docker compose up' to populate it (this project runs on the real dataset only)."
fi

# ---------------------------------------------------------------------------
# Schema migrations -- applied idempotently on EVERY boot (in filename order),
# so an existing volume that just pulled newer code picks up new columns/
# indexes WITHOUT a reseed or `down -v`. This is the key guarantee for anyone
# who "pulls the data": every db/migrations/*.sql is additive and
# IF NOT EXISTS-guarded, so re-applying is a no-op. A migration that targets
# the real-data `mis.*` schema is skipped until mis.* actually exists (a fresh
# clone with no CSVs never creates it, and a bare `ALTER TABLE mis.foo` would
# parse-fail there under `set -e`); detected by whether a NON-comment line of
# the file references `mis.` (so a mere comment mention never mis-classifies a
# public-table migration).
# ---------------------------------------------------------------------------
MIS_PRESENT=$(psql "$DATABASE_URL" -tAc "SELECT to_regclass('mis.trip') IS NOT NULL")
for mig in db/migrations/*.sql; do
    [ -f "$mig" ] || continue
    if grep -vE '^[[:space:]]*--' "$mig" | grep -qiE 'mis\.' && [ "$MIS_PRESENT" != "t" ]; then
        echo "seed: skipping migration $(basename "$mig") -- targets mis.*, not present yet"
        continue
    fi
    echo "seed: applying migration $(basename "$mig")"
    psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f "$mig"
done

echo "seed: done"
