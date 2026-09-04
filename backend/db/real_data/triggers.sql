-- Postgres NOTIFY triggers for the `mis.*` real-data schema -- the
-- equivalent of ../triggers.sql, which only covers the synthetic `public.*`
-- tables. Without these, a live insert into mis.trip/mis.incident/mis.cost/
-- mis.emission only surfaces via the scheduler's interval poll (up to
-- PIPELINE_INTERVAL_MINUTES late), not instantly via the event-driven path --
-- the real-data ingestion switched the active data_contract.yaml to mis.*
-- but never added matching triggers.
--
-- Reuses public.notify_moveinsync_event() unchanged (schema-agnostic: uses
-- TG_TABLE_NAME/NEW.id, and Postgres's default search_path resolves the
-- unqualified function name from `public` regardless of which schema the
-- triggering table lives in) -- so this must be applied AFTER
-- backend/db/schema.sql + backend/db/triggers.sql (which define it), and
-- AFTER backend/db/real_data/ingest.py (mis.* is DROP SCHEMA CASCADE'd and
-- rebuilt on every ingest run, which drops these triggers along with it --
-- re-apply this file after every re-run of ingest.py, same as
-- backend/db/migrations/001_add_escort_and_ack_time.sql).
--
-- Same channel (moveinsync_events), same minimal payload shape -- zero
-- changes needed to backend/app/graph/sense/listener.py::SenseEventListener.
--
-- Apply:
--   psql "$DATABASE_URL" -f backend/db/real_data/triggers.sql

DROP TRIGGER IF EXISTS trg_mis_trip_notify ON mis.trip;
CREATE TRIGGER trg_mis_trip_notify
    AFTER INSERT ON mis.trip
    FOR EACH ROW EXECUTE FUNCTION notify_moveinsync_event('delay_detected');

DROP TRIGGER IF EXISTS trg_mis_incident_notify ON mis.incident;
CREATE TRIGGER trg_mis_incident_notify
    AFTER INSERT ON mis.incident
    FOR EACH ROW EXECUTE FUNCTION notify_moveinsync_event('incident_detected');

DROP TRIGGER IF EXISTS trg_mis_cost_notify ON mis.cost;
CREATE TRIGGER trg_mis_cost_notify
    AFTER INSERT ON mis.cost
    FOR EACH ROW EXECUTE FUNCTION notify_moveinsync_event('cost_detected');

DROP TRIGGER IF EXISTS trg_mis_emission_notify ON mis.emission;
CREATE TRIGGER trg_mis_emission_notify
    AFTER INSERT ON mis.emission
    FOR EACH ROW EXECUTE FUNCTION notify_moveinsync_event('emission_detected');
