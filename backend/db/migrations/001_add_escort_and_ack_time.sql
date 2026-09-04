-- Post-ingest supplement: adds two real source columns to mis.* that
-- backend/db/real_data/{stg_schema.sql,mis_schema.sql,transform.sql,ingest.py}
-- are off-limits to edit for this task, but PRD v3 Feature 1 (Escort
-- Compliance & Real-time Safety Monitor) needs two columns those files load
-- into staging and then drop on the floor during the stg -> mis transform:
--
--   * ride_data_trip.actual_escort  -- present in stg.ride_trip (see
--     stg_schema.sql), never copied into mis.trip by transform.sql's
--     `mis.trip` INSERT.
--   * alerts_data.acknowledge_time  -- present in stg.alerts_data, never
--     copied into mis.incident (mis.incident.reported_at is a same-as-
--     occurred_at placeholder, not a real acknowledgement timestamp -- see
--     mis_schema.sql's mis.incident comment).
--
-- Both are real, non-fabricated columns already sitting in the raw CSVs and
-- in `stg.*` (which ingest.py deliberately does NOT drop until the *next*
-- run -- see backend/db/real_data/README.md: "both stg and mis are DROP
-- SCHEMA...CASCADE'd and rebuilt from scratch on every run", i.e. at the
-- START of the next run, not the end of this one). So rather than touching
-- the forbidden ingestion files, this migration ALTERs the two mis.* tables
-- and backfills straight from the still-live `stg.*` staging tables using
-- the exact same normalisation/dedup rules transform.sql already uses
-- (stg.safe_bool / stg.safe_ts_mdy_hm helper functions it defines, which
-- also persist in the `stg` schema after ingest.py finishes).
--
-- IMPORTANT: because `stg`/`mis` are dropped and rebuilt on every
-- `ingest.py` run, this migration must be RE-APPLIED after every re-run of
-- ingest.py (it is not part of that script, by design/constraint). Safe to
-- run more than once: both ALTERs are IF NOT EXISTS and both UPDATEs are
-- plain idempotent overwrites.

-- ---------------------------------------------------------------------------
-- mis.trip.actual_escort
-- ---------------------------------------------------------------------------
ALTER TABLE mis.trip ADD COLUMN IF NOT EXISTS actual_escort BOOLEAN;

-- Mirrors transform.sql's `mis.trip` INSERT dedup rule exactly (DISTINCT ON
-- tid, ORDER BY tid, actual_end_epoch DESC NULLS LAST, source_month) so a
-- trip_id that collides across two (business_unit, month) pairs (~1.1% of
-- trips, see mis_schema.sql's mis.trip comment) resolves to the SAME source
-- row that transform.sql picked to become this mis.trip row -- otherwise
-- this backfill could silently attach the wrong month's escort flag to a
-- colliding trip_id.
WITH ride_trip_tid AS (
    SELECT
        stg.safe_bigint(trip_id) AS tid,
        actual_escort,
        actual_end_epoch,
        source_month
    FROM stg.ride_trip
    WHERE stg.safe_bigint(trip_id) IS NOT NULL
),
escort_by_trip AS (
    SELECT DISTINCT ON (tid)
        tid,
        stg.safe_bool(actual_escort) AS actual_escort
    FROM ride_trip_tid
    ORDER BY tid, stg.safe_epoch_ts(actual_end_epoch) DESC NULLS LAST, source_month
)
UPDATE mis.trip t
SET actual_escort = e.actual_escort
FROM escort_by_trip e
WHERE e.tid = t.id;

-- ---------------------------------------------------------------------------
-- mis.incident.acknowledge_time
-- ---------------------------------------------------------------------------
ALTER TABLE mis.incident ADD COLUMN IF NOT EXISTS acknowledge_time TIMESTAMPTZ;

-- mis.incident.id = alerts_data.event_id (already a unique natural key, see
-- mis_schema.sql), so this is a plain 1:1 join, no dedup needed.
UPDATE mis.incident i
SET acknowledge_time = stg.safe_ts_mdy_hm(a.acknowledge_time)
FROM stg.alerts_data a
WHERE a.event_id = i.id;
