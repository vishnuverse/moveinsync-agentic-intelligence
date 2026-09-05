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

-- ---------------------------------------------------------------------------
-- SP-B: alert_rules / gate_settings / gate_decisions -- config-driven
-- thresholds, gate policy, and the LLM-filtering gate's audit trail (plan
-- §1/§2/§A1). Same rationale as pipeline_runs/chat_threads above for living
-- here: runtime-editable policy + runtime-written audit rows, not seed/input
-- data -- must stay OUT of generate.py's reset_tables() TRUNCATE list, and
-- must survive a reseed untouched.
--
-- alert_rules: one row per (org_id, signal_type). `params` is a JSONB blob
-- keyed by the exact keyword-argument names the sense/nodes.py detector
-- functions already accept -- the tunable set is heterogeneous per signal
-- type (delay_breach has 1 param, attendance_correlation has 5), so a fixed-
-- column table would need ~15 mostly-NULL columns; a JSONB blob needs no
-- migration when a new tunable is added to one detector later. No seed data
-- is required: app.rules.get_rules()/get_gate_settings() fall back to
-- sense/nodes.py's hardcoded DEFAULT_* module constants when no row exists
-- for an (org_id, signal_type) -- those constants become fallback defaults,
-- never deleted.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS alert_rules (
    id                   BIGSERIAL PRIMARY KEY,
    org_id               TEXT NOT NULL DEFAULT 'moveinsync-demo',
    signal_type          TEXT NOT NULL,
    params               JSONB NOT NULL DEFAULT '{}'::jsonb,
    gate_mode            TEXT NOT NULL DEFAULT 'auto' CHECK (gate_mode IN ('auto', 'force_suppress', 'force_rule_only', 'force_escalate')),
    notification_cadence TEXT NOT NULL DEFAULT 'immediate' CHECK (notification_cadence IN ('immediate', 'hourly', 'every_2_hours', 'daily', 'weekly')),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by           TEXT,
    UNIQUE (org_id, signal_type)
);

CREATE INDEX IF NOT EXISTS idx_alert_rules_org_id ON alert_rules(org_id);

-- gate_settings: one row per org -- global gate policy (not per-signal-type).
-- escalation_after_hours_* back the §A1 escalation-hierarchy timeout check;
-- kept on this table (not alert_rules) since, like the other columns here,
-- they're global gate policy, not per-signal-type params.
CREATE TABLE IF NOT EXISTS gate_settings (
    org_id                          TEXT PRIMARY KEY DEFAULT 'moveinsync-demo',
    recurrence_window_hours         INT NOT NULL DEFAULT 24,
    recurrence_suppress_after       INT NOT NULL DEFAULT 3,
    max_consecutive_suppressions    INT NOT NULL DEFAULT 5,
    rule_only_margin_ratio          NUMERIC(5,2) NOT NULL DEFAULT 2.0,
    max_fp_rate_for_rule_only       NUMERIC(4,3) NOT NULL DEFAULT 0.20,
    min_confidence_for_rule_only    NUMERIC(4,3) NOT NULL DEFAULT 0.60,
    max_healthy_suppression_rate    NUMERIC(4,3) NOT NULL DEFAULT 0.80,
    escalation_after_hours_critical NUMERIC(5,2) NOT NULL DEFAULT 1.0,
    escalation_after_hours_high     NUMERIC(5,2) NOT NULL DEFAULT 4.0,
    escalation_after_hours_medium   NUMERIC(5,2) NOT NULL DEFAULT 24.0,
    updated_at                      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by                      TEXT
);

-- gate_decisions: one row per (signal, persona) evaluation, EVERY tick,
-- regardless of the resulting action -- a `suppress` decision never produces
-- an agent_notifications row, so this table is the only place recurrence
-- (and the suppression-heartbeat/suppression-rate checks) can be tracked.
-- `thread_id` is stored on every row (including suppress, computed before
-- the gate runs) so it can JOIN to agent_notifications.thread_id for the
-- false-positive-rate query (gate_stats.py, shared by gate.py itself and
-- GET /api/settings/usage).
CREATE TABLE IF NOT EXISTS gate_decisions (
    id            BIGSERIAL PRIMARY KEY,
    org_id        TEXT NOT NULL DEFAULT 'moveinsync-demo',
    persona       TEXT NOT NULL CHECK (persona IN ('transport_manager', 'line_manager', 'transport_head')),
    signal_type   TEXT NOT NULL,
    scope         TEXT NOT NULL,
    entity_id     TEXT,
    severity      TEXT,
    action        TEXT NOT NULL CHECK (action IN ('suppress', 'rule_only', 'escalate')),
    reason        TEXT NOT NULL,
    matched_rule  TEXT NOT NULL,
    confidence    NUMERIC(4,3),
    thread_id     TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_gate_decisions_org_created ON gate_decisions(org_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_gate_decisions_scope_lookup ON gate_decisions(org_id, persona, signal_type, scope, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_gate_decisions_thread_id ON gate_decisions(thread_id);
