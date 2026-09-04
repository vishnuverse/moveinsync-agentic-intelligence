-- Postgres NOTIFY triggers for the sense layer's async LISTEN/NOTIFY eventing
-- (plan §4). Apply AFTER backend/db/schema.sql -- kept in this separate file
-- (rather than added to schema.sql) so it doesn't collide with the
-- schema-owning agent's file.
--
-- Channel: moveinsync_events. Consumed by
-- backend/app/graph/sense/listener.py::SenseEventListener.
--
-- Payload is deliberately minimal -- event type, table name, primary key --
-- well under Postgres's 8000-byte NOTIFY limit. The listener re-queries for
-- full row detail rather than cramming it into the payload.
--
-- Apply:
--   psql "$DATABASE_URL" -f backend/db/schema.sql
--   psql "$DATABASE_URL" -f backend/db/triggers.sql

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

DROP TRIGGER IF EXISTS trg_route_trips_notify ON route_trips;
CREATE TRIGGER trg_route_trips_notify
    AFTER INSERT ON route_trips
    FOR EACH ROW EXECUTE FUNCTION notify_moveinsync_event('delay_detected');

DROP TRIGGER IF EXISTS trg_safety_incidents_notify ON safety_incidents;
CREATE TRIGGER trg_safety_incidents_notify
    AFTER INSERT ON safety_incidents
    FOR EACH ROW EXECUTE FUNCTION notify_moveinsync_event('incident_detected');

DROP TRIGGER IF EXISTS trg_route_costs_notify ON route_costs;
CREATE TRIGGER trg_route_costs_notify
    AFTER INSERT ON route_costs
    FOR EACH ROW EXECUTE FUNCTION notify_moveinsync_event('cost_detected');

DROP TRIGGER IF EXISTS trg_emissions_log_notify ON emissions_log;
CREATE TRIGGER trg_emissions_log_notify
    AFTER INSERT ON emissions_log
    FOR EACH ROW EXECUTE FUNCTION notify_moveinsync_event('emission_detected');
