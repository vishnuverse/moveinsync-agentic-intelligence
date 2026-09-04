-- API-layer addition to backend/db/schema.sql (plan §10/§11's `GET /api/activity`
-- endpoint needs SOME persistence of pipeline-run history that doesn't exist yet
-- as a dedicated table). Kept in a separate file rather than editing schema.sql
-- directly, same convention backend/db/triggers.sql already established for
-- staying out of the schema-owning agent's file.
--
-- Apply AFTER schema.sql (+ triggers.sql):
--   psql "$DATABASE_URL" -f backend/db/schema.sql
--   psql "$DATABASE_URL" -f backend/db/triggers.sql
--   psql "$DATABASE_URL" -f backend/db/api_schema.sql
--
-- Written to by app/schedulers/interval.py's _tick and
-- app/schedulers/listener_bridge.py's run_listener_bridge, one row per
-- (signal, persona) dispatch app.graph.supervisor.run_pipeline produces --
-- see app/services/activity_log.py. Read by GET /api/activity
-- (app/api/activity.py), system-wide, not persona-filtered.

CREATE TABLE IF NOT EXISTS pipeline_runs (
    id            BIGSERIAL PRIMARY KEY,
    org_id        TEXT NOT NULL DEFAULT 'moveinsync-demo',
    persona       TEXT NOT NULL CHECK (persona IN ('transport_manager', 'line_manager', 'transport_head')),
    action        TEXT NOT NULL,
    thread_id     TEXT,
    triggered_by  TEXT NOT NULL CHECK (triggered_by IN ('schedule', 'event')),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_pipeline_runs_org_id ON pipeline_runs(org_id);
CREATE INDEX IF NOT EXISTS idx_pipeline_runs_created_at ON pipeline_runs(created_at DESC);
