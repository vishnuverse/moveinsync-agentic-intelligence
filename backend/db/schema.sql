-- MoveInSync Agentic Intelligence -- app/infra/reference Postgres schema (plan §7).
--
-- The real business data lives in the `mis` schema (backend/db/real_data/
-- mis_schema.sql, populated by ingest.py from the real dataset), which is what
-- the active data contract (backend/config/data_contract.yaml) points at. This
-- file no longer defines synthetic business tables (route_trips, vendors,
-- commute_logs, ...) -- the project runs solely on the real `mis.*` data. What
-- remains here are the tables that are NOT business data and must exist in
-- `public` regardless of the data source:
--   * sustainability_targets -- reference benchmarks the reason layer reads by
--     literal name (research_agent, emissions detector)
--   * data_quality_flags     -- ingest/runtime data-quality log (transform.sql
--     during real ingest + sense/nodes.py flag_data_quality at runtime)
--   * agent_notifications / agent_reports -- act-layer outputs the UI reads
-- LangGraph checkpoint tables and the LangMem `store` table are created at
-- runtime by those libraries, not here.
--
-- Every table carries org_id for multi-tenancy.

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ---------------------------------------------------------------------------
-- sustainability_targets -- reference benchmarks (plan §8 concrete benchmarks).
-- Seeded by backend/db/seed/generate.py; read by the reason layer to judge a
-- metric good/bad against an external target.
-- ---------------------------------------------------------------------------

CREATE TABLE sustainability_targets (
    id               BIGSERIAL PRIMARY KEY,
    org_id           TEXT NOT NULL DEFAULT 'moveinsync-demo',
    metric_name      TEXT NOT NULL,
    target_value     NUMERIC(10, 3) NOT NULL,
    threshold_value  NUMERIC(10, 3),
    unit             TEXT NOT NULL,
    period           TEXT NOT NULL DEFAULT 'ongoing',
    effective_from   DATE NOT NULL DEFAULT CURRENT_DATE,
    effective_to     DATE,
    notes            TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_sustainability_targets_org_id ON sustainability_targets(org_id);

-- ---------------------------------------------------------------------------
-- data_quality_flags -- ingest/runtime data-quality issues are logged here,
-- never silently dropped. Written by real_data/transform.sql (bulk ingest) and
-- sense/nodes.py flag_data_quality (runtime). Referenced by literal name.
-- ---------------------------------------------------------------------------

CREATE TABLE data_quality_flags (
    id            BIGSERIAL PRIMARY KEY,
    org_id        TEXT NOT NULL DEFAULT 'moveinsync-demo',
    source_table  TEXT NOT NULL,
    source_pk     TEXT,
    issue_type    TEXT NOT NULL CHECK (issue_type IN ('null_required_field', 'malformed_timestamp', 'duplicate_row', 'out_of_range_value', 'orphaned_reference', 'other')),
    issue_detail  TEXT,
    severity      TEXT NOT NULL DEFAULT 'low' CHECK (severity IN ('low', 'medium', 'high')),
    detected_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved      BOOLEAN NOT NULL DEFAULT FALSE,
    resolved_at   TIMESTAMPTZ,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_data_quality_flags_org_id ON data_quality_flags(org_id);
CREATE INDEX idx_data_quality_flags_source_table ON data_quality_flags(source_table);
CREATE INDEX idx_data_quality_flags_resolved ON data_quality_flags(resolved);

-- ---------------------------------------------------------------------------
-- agent_notifications / agent_reports -- act-layer outputs the UI reads
-- ---------------------------------------------------------------------------

CREATE TABLE agent_notifications (
    id                     BIGSERIAL PRIMARY KEY,
    org_id                 TEXT NOT NULL DEFAULT 'moveinsync-demo',
    persona                TEXT NOT NULL CHECK (persona IN ('transport_manager', 'line_manager', 'transport_head')),
    scope                  TEXT NOT NULL DEFAULT 'global',
    severity               TEXT NOT NULL DEFAULT 'info' CHECK (severity IN ('info', 'warning', 'critical')),
    title                  TEXT NOT NULL,
    message                TEXT NOT NULL,
    related_entity_type    TEXT,
    related_entity_id      BIGINT,
    status                 TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'acked', 'needs_intervention', 'resolved')),
    thread_id              TEXT,
    -- SP-B additions (see db/migrations/003_add_false_positive_feedback.sql,
    -- 004_add_notification_cadence.sql, 005_add_escalation_columns.sql --
    -- kept here too, mirroring 002's convention, so a FRESH db built from
    -- this file already has them; those three migration files remain the
    -- way to bring an ALREADY-SEEDED volume up to date without a reseed).
    is_false_positive        BOOLEAN NOT NULL DEFAULT FALSE,
    false_positive_note      TEXT,
    false_positive_marked_at TIMESTAMPTZ,
    scheduled_for             TIMESTAMPTZ,
    escalated_at              TIMESTAMPTZ,
    escalated_to_persona      TEXT,
    created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at             TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_agent_notifications_org_id ON agent_notifications(org_id);
CREATE INDEX idx_agent_notifications_persona ON agent_notifications(persona);
CREATE INDEX idx_agent_notifications_status ON agent_notifications(status);
CREATE INDEX idx_agent_notifications_thread_id ON agent_notifications(thread_id);
-- Composite index for the paginated per-persona inbox read (see
-- db/migrations/002_pagination_indexes.sql); kept here so fresh DBs built
-- from this schema get it without the migration.
CREATE INDEX IF NOT EXISTS idx_agent_notifications_org_persona_created ON agent_notifications (org_id, persona, created_at DESC);
-- SP-B: cadence visibility filter (list_notifications/count_notifications
-- add `scheduled_for IS NULL OR scheduled_for <= now()`) and false-positive
-- lookups (dashboard/settings rate queries).
CREATE INDEX IF NOT EXISTS idx_agent_notifications_org_persona_scheduled ON agent_notifications (org_id, persona, scheduled_for);
CREATE INDEX IF NOT EXISTS idx_agent_notifications_false_positive ON agent_notifications (org_id, is_false_positive) WHERE is_false_positive;
-- SP-B: escalation-hierarchy check's scan predicate (open, not yet escalated).
CREATE INDEX IF NOT EXISTS idx_agent_notifications_escalation_scan ON agent_notifications (org_id, status, severity, created_at) WHERE escalated_at IS NULL;

CREATE TABLE agent_reports (
    id            BIGSERIAL PRIMARY KEY,
    org_id        TEXT NOT NULL DEFAULT 'moveinsync-demo',
    report_type   TEXT NOT NULL CHECK (report_type IN ('daily_digest', 'weekly_digest', 'monthly_leadership', 'quarterly_leadership', 'ad_hoc')),
    persona       TEXT NOT NULL CHECK (persona IN ('transport_manager', 'line_manager', 'transport_head')),
    title         TEXT NOT NULL,
    period_start  DATE,
    period_end    DATE,
    storage_ref   TEXT NOT NULL,
    format        TEXT NOT NULL DEFAULT 'html' CHECK (format IN ('html', 'pdf')),
    thread_id     TEXT,
    generated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_agent_reports_org_id ON agent_reports(org_id);
CREATE INDEX idx_agent_reports_persona ON agent_reports(persona);
CREATE INDEX idx_agent_reports_report_type ON agent_reports(report_type);
-- Composite index for the paginated per-persona report list read (see
-- db/migrations/002_pagination_indexes.sql); kept here so fresh DBs built
-- from this schema get it without the migration.
CREATE INDEX IF NOT EXISTS idx_agent_reports_org_persona_generated ON agent_reports (org_id, persona, generated_at DESC);
