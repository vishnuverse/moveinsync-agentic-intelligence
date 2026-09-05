-- MoveInSync Agentic Intelligence -- reference/default Postgres schema (plan §7).
--
-- This is the schema the data contract (backend/config/data_contract.yaml) points
-- at out of the box. Table/column names here MUST stay in lockstep with that file --
-- they are two views of the same design. Flat, single-column foreign keys, no deep
-- nesting on purpose: this schema is expected to change once real MoveInSync data
-- replaces the synthetic seed.
--
-- Every table carries org_id for multi-tenancy even though the hackathon runs a
-- single org ('moveinsync-demo') -- retrofitting tenancy later is the expensive path.

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ---------------------------------------------------------------------------
-- teams / employees (mutually referential: created teams first without the
-- employees FK, added back via ALTER TABLE once employees exists)
-- ---------------------------------------------------------------------------

CREATE TABLE teams (
    id              BIGSERIAL PRIMARY KEY,
    org_id          TEXT NOT NULL DEFAULT 'moveinsync-demo',
    name            TEXT NOT NULL,
    region          TEXT NOT NULL,
    line_manager_id BIGINT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE employees (
    id               BIGSERIAL PRIMARY KEY,
    org_id           TEXT NOT NULL DEFAULT 'moveinsync-demo',
    employee_code    TEXT NOT NULL,
    full_name        TEXT NOT NULL,
    email            TEXT,
    team_id          BIGINT REFERENCES teams(id),
    line_manager_id  BIGINT REFERENCES employees(id),
    home_location    TEXT,
    work_location    TEXT,
    status           TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'inactive', 'on_leave')),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (org_id, employee_code)
);

ALTER TABLE teams
    ADD CONSTRAINT fk_teams_line_manager FOREIGN KEY (line_manager_id) REFERENCES employees(id);

CREATE INDEX idx_teams_org_id ON teams(org_id);
CREATE INDEX idx_employees_org_id ON employees(org_id);
CREATE INDEX idx_employees_team_id ON employees(team_id);
CREATE INDEX idx_employees_line_manager_id ON employees(line_manager_id);

-- ---------------------------------------------------------------------------
-- vendors / routes / drivers
-- ---------------------------------------------------------------------------

CREATE TABLE vendors (
    id                  BIGSERIAL PRIMARY KEY,
    org_id              TEXT NOT NULL DEFAULT 'moveinsync-demo',
    name                TEXT NOT NULL,
    contract_start_date DATE,
    contract_end_date   DATE,
    sla_target_pct      NUMERIC(5, 2) NOT NULL DEFAULT 95.00,
    cost_per_km_inr     NUMERIC(8, 2),
    region              TEXT NOT NULL,
    status              TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'inactive', 'probation')),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_vendors_org_id ON vendors(org_id);

CREATE TABLE routes (
    id                     BIGSERIAL PRIMARY KEY,
    org_id                 TEXT NOT NULL DEFAULT 'moveinsync-demo',
    route_code             TEXT NOT NULL,
    name                   TEXT NOT NULL,
    origin                 TEXT NOT NULL,
    destination             TEXT NOT NULL,
    region                 TEXT NOT NULL,
    vendor_id              BIGINT REFERENCES vendors(id),
    scheduled_distance_km  NUMERIC(6, 2) NOT NULL,
    shift_type             TEXT NOT NULL DEFAULT 'general' CHECK (shift_type IN ('general', 'morning', 'evening', 'night')),
    status                 TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'suspended', 'retired')),
    created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (org_id, route_code)
);

CREATE INDEX idx_routes_org_id ON routes(org_id);
CREATE INDEX idx_routes_vendor_id ON routes(vendor_id);

CREATE TABLE drivers (
    id              BIGSERIAL PRIMARY KEY,
    org_id          TEXT NOT NULL DEFAULT 'moveinsync-demo',
    vendor_id       BIGINT REFERENCES vendors(id),
    driver_code     TEXT NOT NULL,
    full_name       TEXT NOT NULL,
    license_number  TEXT,
    phone           TEXT,
    status          TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'inactive', 'suspended')),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (org_id, driver_code)
);

CREATE INDEX idx_drivers_org_id ON drivers(org_id);
CREATE INDEX idx_drivers_vendor_id ON drivers(vendor_id);

-- ---------------------------------------------------------------------------
-- route_trips -- core timeliness fact table
-- ---------------------------------------------------------------------------

CREATE TABLE route_trips (
    id                  BIGSERIAL PRIMARY KEY,
    org_id              TEXT NOT NULL DEFAULT 'moveinsync-demo',
    route_id            BIGINT NOT NULL REFERENCES routes(id),
    driver_id           BIGINT REFERENCES drivers(id),
    trip_date           DATE NOT NULL,
    scheduled_departure TIMESTAMPTZ,
    scheduled_arrival   TIMESTAMPTZ,
    actual_departure    TIMESTAMPTZ,
    actual_arrival      TIMESTAMPTZ,
    passenger_count     INTEGER NOT NULL DEFAULT 0,
    status              TEXT NOT NULL DEFAULT 'completed' CHECK (status IN ('scheduled', 'in_progress', 'completed', 'cancelled')),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_route_trips_org_id ON route_trips(org_id);
CREATE INDEX idx_route_trips_route_id ON route_trips(route_id);
CREATE INDEX idx_route_trips_driver_id ON route_trips(driver_id);
CREATE INDEX idx_route_trips_trip_date ON route_trips(trip_date);
CREATE INDEX idx_route_trips_scheduled_arrival ON route_trips(scheduled_arrival);

-- ---------------------------------------------------------------------------
-- safety_incidents
-- ---------------------------------------------------------------------------

CREATE TABLE safety_incidents (
    id             BIGSERIAL PRIMARY KEY,
    org_id         TEXT NOT NULL DEFAULT 'moveinsync-demo',
    trip_id        BIGINT REFERENCES route_trips(id),
    route_id       BIGINT NOT NULL REFERENCES routes(id),
    driver_id      BIGINT REFERENCES drivers(id),
    incident_type  TEXT NOT NULL CHECK (incident_type IN ('accident', 'breakdown', 'harassment', 'speeding', 'route_deviation', 'other')),
    severity       TEXT NOT NULL CHECK (severity IN ('low', 'medium', 'high', 'critical')),
    description    TEXT,
    occurred_at    TIMESTAMPTZ NOT NULL,
    reported_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    status         TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'investigating', 'resolved', 'closed')),
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_safety_incidents_org_id ON safety_incidents(org_id);
CREATE INDEX idx_safety_incidents_route_id ON safety_incidents(route_id);
CREATE INDEX idx_safety_incidents_trip_id ON safety_incidents(trip_id);
CREATE INDEX idx_safety_incidents_occurred_at ON safety_incidents(occurred_at);

-- ---------------------------------------------------------------------------
-- route_costs / vendor_invoices
-- ---------------------------------------------------------------------------

CREATE TABLE route_costs (
    id               BIGSERIAL PRIMARY KEY,
    org_id           TEXT NOT NULL DEFAULT 'moveinsync-demo',
    route_id         BIGINT NOT NULL REFERENCES routes(id),
    vendor_id        BIGINT REFERENCES vendors(id),
    trip_id          BIGINT REFERENCES route_trips(id),
    cost_date        DATE NOT NULL,
    distance_km      NUMERIC(6, 2),
    passenger_count  INTEGER,
    total_cost_inr   NUMERIC(10, 2) NOT NULL,
    cost_per_km_inr  NUMERIC(8, 2),
    cost_category    TEXT NOT NULL DEFAULT 'fuel' CHECK (cost_category IN ('fuel', 'toll', 'driver', 'maintenance', 'penalty', 'other')),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_route_costs_org_id ON route_costs(org_id);
CREATE INDEX idx_route_costs_route_id ON route_costs(route_id);
CREATE INDEX idx_route_costs_vendor_id ON route_costs(vendor_id);
CREATE INDEX idx_route_costs_cost_date ON route_costs(cost_date);

CREATE TABLE vendor_invoices (
    id                    BIGSERIAL PRIMARY KEY,
    org_id                TEXT NOT NULL DEFAULT 'moveinsync-demo',
    vendor_id             BIGINT NOT NULL REFERENCES vendors(id),
    invoice_number        TEXT NOT NULL,
    billing_period_start  DATE NOT NULL,
    billing_period_end    DATE NOT NULL,
    amount_inr            NUMERIC(12, 2) NOT NULL,
    sla_penalty_inr       NUMERIC(10, 2) NOT NULL DEFAULT 0,
    status                TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'paid', 'disputed')),
    issued_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    paid_at               TIMESTAMPTZ,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (org_id, invoice_number)
);

CREATE INDEX idx_vendor_invoices_org_id ON vendor_invoices(org_id);
CREATE INDEX idx_vendor_invoices_vendor_id ON vendor_invoices(vendor_id);

-- ---------------------------------------------------------------------------
-- emissions_log / sustainability_targets
-- ---------------------------------------------------------------------------

CREATE TABLE emissions_log (
    id                     BIGSERIAL PRIMARY KEY,
    org_id                 TEXT NOT NULL DEFAULT 'moveinsync-demo',
    route_id               BIGINT NOT NULL REFERENCES routes(id),
    trip_id                BIGINT REFERENCES route_trips(id),
    log_date               DATE NOT NULL,
    distance_km            NUMERIC(6, 2),
    passenger_count        INTEGER,
    co2_grams              NUMERIC(10, 2) NOT NULL,
    co2_per_passenger_km   NUMERIC(8, 3),
    vehicle_type           TEXT NOT NULL DEFAULT 'ICE' CHECK (vehicle_type IN ('ICE', 'EV', 'hybrid')),
    created_at             TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_emissions_log_org_id ON emissions_log(org_id);
CREATE INDEX idx_emissions_log_route_id ON emissions_log(route_id);
CREATE INDEX idx_emissions_log_log_date ON emissions_log(log_date);

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
-- commute_logs / attendance_records
-- ---------------------------------------------------------------------------

CREATE TABLE commute_logs (
    id             BIGSERIAL PRIMARY KEY,
    org_id         TEXT NOT NULL DEFAULT 'moveinsync-demo',
    employee_id    BIGINT NOT NULL REFERENCES employees(id),
    trip_id        BIGINT REFERENCES route_trips(id),
    route_id       BIGINT REFERENCES routes(id),
    log_date       DATE NOT NULL,
    boarding_time  TIMESTAMPTZ,
    alighting_time TIMESTAMPTZ,
    mode           TEXT NOT NULL DEFAULT 'shuttle' CHECK (mode IN ('shuttle', 'cab', 'walk_in', 'wfh')),
    status         TEXT NOT NULL DEFAULT 'completed' CHECK (status IN ('completed', 'missed', 'cancelled')),
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_commute_logs_org_id ON commute_logs(org_id);
CREATE INDEX idx_commute_logs_employee_id ON commute_logs(employee_id);
CREATE INDEX idx_commute_logs_log_date ON commute_logs(log_date);

CREATE TABLE attendance_records (
    id              BIGSERIAL PRIMARY KEY,
    org_id          TEXT NOT NULL DEFAULT 'moveinsync-demo',
    employee_id     BIGINT NOT NULL REFERENCES employees(id),
    work_date       DATE NOT NULL,
    clock_in_time   TIMESTAMPTZ,
    clock_out_time  TIMESTAMPTZ,
    status          TEXT NOT NULL DEFAULT 'present' CHECK (status IN ('present', 'late', 'absent', 'wfh', 'on_leave')),
    late_minutes    INTEGER NOT NULL DEFAULT 0,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_attendance_records_org_id ON attendance_records(org_id);
CREATE INDEX idx_attendance_records_employee_id ON attendance_records(employee_id);
CREATE INDEX idx_attendance_records_work_date ON attendance_records(work_date);

-- ---------------------------------------------------------------------------
-- data_quality_flags -- ingest issues are logged here, never silently dropped
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
    id                   BIGSERIAL PRIMARY KEY,
    org_id               TEXT NOT NULL DEFAULT 'moveinsync-demo',
    persona              TEXT NOT NULL CHECK (persona IN ('transport_manager', 'line_manager', 'transport_head')),
    scope                TEXT NOT NULL DEFAULT 'global',
    severity             TEXT NOT NULL DEFAULT 'info' CHECK (severity IN ('info', 'warning', 'critical')),
    title                TEXT NOT NULL,
    message              TEXT NOT NULL,
    related_entity_type  TEXT,
    related_entity_id    BIGINT,
    status               TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'acked', 'needs_intervention', 'resolved')),
    thread_id            TEXT,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_agent_notifications_org_id ON agent_notifications(org_id);
CREATE INDEX idx_agent_notifications_persona ON agent_notifications(persona);
CREATE INDEX idx_agent_notifications_status ON agent_notifications(status);
CREATE INDEX idx_agent_notifications_thread_id ON agent_notifications(thread_id);
-- Composite index for the paginated per-persona inbox read (see
-- db/migrations/002_pagination_indexes.sql); kept here so fresh DBs built
-- from this schema get it without the migration.
CREATE INDEX IF NOT EXISTS idx_agent_notifications_org_persona_created ON agent_notifications (org_id, persona, created_at DESC);

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

-- ---------------------------------------------------------------------------
-- sql_agent_examples -- pgvector-backed NL->SQL few-shot grounding (optional RAG layer)
-- ---------------------------------------------------------------------------

CREATE TABLE sql_agent_examples (
    id             BIGSERIAL PRIMARY KEY,
    org_id         TEXT NOT NULL DEFAULT 'moveinsync-demo',
    question       TEXT NOT NULL,
    sql            TEXT NOT NULL,
    table_context  TEXT,
    embedding      vector(1536),
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_sql_agent_examples_org_id ON sql_agent_examples(org_id);
CREATE INDEX idx_sql_agent_examples_embedding ON sql_agent_examples USING ivfflat (embedding vector_cosine_ops) WITH (lists = 10);
