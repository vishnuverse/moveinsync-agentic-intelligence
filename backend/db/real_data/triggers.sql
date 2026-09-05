-- Postgres NOTIFY triggers for the `mis.*` real-data schema -- the sense
-- layer's async LISTEN/NOTIFY eventing (plan §4). Without these, a live insert
-- into mis.trip/mis.incident/mis.cost/mis.emission only surfaces via the
-- scheduler's interval poll (up to PIPELINE_INTERVAL_MINUTES late), not
-- instantly via the event-driven path.
--
-- This file is SELF-CONTAINED: it defines notify_moveinsync_event() itself.
-- (It used to rely on the synthetic backend/db/triggers.sql for that function,
-- but that file was removed when the project moved to real-data-only -- see
-- backend/db/schema.sql. The function is defined unqualified, i.e. in `public`,
-- and resolves via the default search_path for triggers on mis.* tables.)
--
-- mis.* is DROP SCHEMA CASCADE'd and rebuilt on every ingest.py run, which
-- drops these triggers with it -- so this file is re-applied after every
-- ingest (see backend/db/seed/entrypoint.sh), same as
-- backend/db/migrations/001_add_escort_and_ack_time.sql.
--
-- Channel: moveinsync_events. Consumed by
-- backend/app/graph/sense/listener.py::SenseEventListener. Payload is minimal
-- (event type, table name, primary key) -- well under Postgres's 8000-byte
-- NOTIFY limit; the listener re-queries for full row detail.
--
-- Apply:
--   psql "$DATABASE_URL" -f backend/db/real_data/triggers.sql

CREATE OR REPLACE FUNCTION notify_moveinsync_event() RETURNS trigger AS $$
DECLARE
    event_name TEXT := TG_ARGV[0];
    payload TEXT;
BEGIN
    payload := json_build_object(
        'event', event_name,
        'table', TG_TABLE_NAME,
        'id', NEW.id
    )::text;
    PERFORM pg_notify('moveinsync_events', payload);
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

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
