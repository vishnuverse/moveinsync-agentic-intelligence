-- API-layer addition to backend/db/schema.sql (plan §10/§11's `GET /api/activity`
-- endpoint needs SOME persistence of pipeline-run history that doesn't exist yet
-- as a dedicated table). Kept in a separate file rather than editing schema.sql
-- directly, to stay out of the schema-owning file.
--
-- Apply AFTER schema.sql:
--   psql "$DATABASE_URL" -f backend/db/schema.sql
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

-- ---------------------------------------------------------------------------
-- chat_threads -- chat history feature (thread list/rename/delete + the
-- "select something to chat with" scope picker). Same rationale as
-- pipeline_runs above for living here instead of schema.sql: this is
-- runtime output the chat API itself writes (app/services/chat_threads.py),
-- not seed/input data, so it must stay OUT of generate.py's reset_tables()
-- TRUNCATE list, synthetic or real-data mode alike -- re-applying this file
-- (CREATE TABLE IF NOT EXISTS) on a reseed must never touch existing rows.
--
-- `id` is TEXT and IS the LangGraph checkpoint thread_id
-- (app.graph.supervisor.build_thread_id's output: "{persona}:chat:{ref}"),
-- not a separate surrogate key -- one identifier space end-to-end, so a
-- thread's own Trace Drawer resolves straight from chat_threads.id to
-- checkpoint history with no translation table. It also doubles as the
-- LangMem episodic-memory namespace's `user_id` segment (see
-- app/memory/episodic.py's forget_thread and app/api/chat.py's module
-- docstring for why), which is why deleting a thread must delete its
-- episodic memory too -- this table alone does not hold the conversation
-- content, only its identity/title/scope metadata.
--
-- scope_entity_type/scope_entity_id are nullable and deliberately loose
-- (TEXT, not a FK) -- they hold a human/DB-grounded display value (a vendor
-- name, a route_code, a team name, a region) sourced from
-- app/services/scope_options.py, not a numeric surrogate key, so the chat
-- endpoint can prepend it straight into the NL question ("Regarding vendor
-- 'X': ...") without a join back to the source table.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS chat_threads (
    id                 TEXT PRIMARY KEY,
    org_id             TEXT NOT NULL DEFAULT 'moveinsync-demo',
    persona            TEXT NOT NULL CHECK (persona IN ('transport_manager', 'line_manager', 'transport_head')),
    title              TEXT NOT NULL,
    scope_entity_type  TEXT,
    scope_entity_id    TEXT,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_chat_threads_org_persona ON chat_threads(org_id, persona);
CREATE INDEX IF NOT EXISTS idx_chat_threads_updated_at ON chat_threads(updated_at DESC);
