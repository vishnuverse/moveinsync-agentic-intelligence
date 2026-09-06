-- Real-data canonical schema for the MoveInSync Agentic Intelligence data
-- contract (plan §3). Populated by backend/db/real_data/ingest.py from the
-- real dataset in data/*.csv via backend/db/real_data/transform.sql.
--
-- Lives in its own `mis` schema so it never collides with the synthetic
-- `public` schema from backend/db/schema.sql -- both can be present in the
-- same database at once; which one the app reads is purely a matter of which
-- backend/config/data_contract*.yaml is active (see loader.py's
-- DATA_CONTRACT_PATH env var).
--
-- `data_quality_flags` and `sustainability_targets` are NOT duplicated here:
-- sense/nodes.py references those two by literal name (a deliberate, narrow
-- exception documented in that module's docstring, since they are fixed
-- infra/reference tables, not logical business entities in the contract) and
-- relies on the default `public` search_path resolving them -- so both the
-- synthetic and real-data paths share the same `public.data_quality_flags`
-- and `public.sustainability_targets` tables. backend/db/schema.sql must be
-- applied before this file for those two tables (and the vector/pgcrypto
-- extensions) to exist.
--
-- Tables here are real tables, not views, for query performance at real row
-- counts (~615K trips, ~1.64M employee-legs, ~621K bill lines, ~52K alerts,
-- ~513K feedback rows). Physical FK constraints are only declared where the
-- referenced dimension is derived from the very same source rows being
-- inserted (so it always resolves) or is fully synthetic (team/employee
-- hierarchy). Cross-file `trip_id` links (cost/incident/feedback -> trip,
-- commute -> trip) are deliberately NOT hard-FK-enforced: this is real,
-- messy, multi-file data and a bulk load must not abort because one
-- `bill_data` row's `trip_id` has no matching `ride_data_trip` row. Orphans
-- are resolved via LEFT JOIN during transform and logged into
-- `data_quality_flags` (issue_type = 'orphaned_reference') instead.

DROP SCHEMA IF EXISTS mis CASCADE;
CREATE SCHEMA mis;

-- ---------------------------------------------------------------------------
-- mis.vendor -- no vendor master table exists in the raw data; vendor_id
-- (ride_data_trip) and vendor (bill_data) are free-text names, ~23-24
-- distinct, matched here by case/whitespace-normalised name (norm_name).
-- cost_per_km and on-time-rate ("sla_target_pct") are computed from real
-- billing/trip data during transform, never invented.
-- ---------------------------------------------------------------------------
CREATE TABLE mis.vendor (
    id                   BIGSERIAL PRIMARY KEY,
    org_id               TEXT NOT NULL,          -- business_unit this vendor is seen most often under; a vendor CAN legitimately serve >1 BU in reality, this picks the dominant one since the contract models vendor as single-org-scoped like every other entity
    name                 TEXT NOT NULL,
    norm_name             TEXT NOT NULL UNIQUE,   -- lower(trim(name)); the actual reconciliation key used to join ride_data_trip.vendor_id and bill_data.vendor
    contract_start_date  DATE,                    -- not present in raw data; column kept for contract-shape parity, always NULL
    contract_end_date    DATE,
    sla_target_pct       NUMERIC(5, 2),           -- observed on-time rate (actual_time within 15 min of scheduled_time) from mis.trip, used as the reliability proxy since no contracted SLA target exists in the raw data
    cost_per_km_inr      NUMERIC(12, 4),          -- AVG(trip_cost / total_trip_km) from bill_data, excluding total_trip_km <= 0.1km rows (see mis.cost comment on the divide-by-zero guard)
    region                TEXT,
    status                TEXT NOT NULL DEFAULT 'active',
    created_at             TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_mis_vendor_org_id ON mis.vendor(org_id);

-- ---------------------------------------------------------------------------
-- mis.route -- no route master exists either. Grain chosen is distinct
-- (business_unit, office, product_type): shift_type has ~100 distinct HH:MM
-- values (too granular to be "a route"), and there is no other grouping key
-- in the raw data that reads as a route. vendor_id is the modal vendor
-- observed on that (bu, office, product_type) combination -- an
-- approximation, since a real route can be served by several vendors.
-- ---------------------------------------------------------------------------
CREATE TABLE mis.route (
    id                      BIGSERIAL PRIMARY KEY,
    org_id                  TEXT NOT NULL,        -- = business_unit
    route_code              TEXT NOT NULL UNIQUE, -- deterministic slug of (business_unit, office, product_type)
    name                    TEXT NOT NULL,        -- "<office> <product_type>"
    origin                  TEXT NOT NULL,        -- = office; raw data has no real geocoded origin/destination pair
    destination             TEXT NOT NULL,        -- generic label ("Employee commute"); not a real endpoint, documented approximation
    region                  TEXT NOT NULL,        -- = business_unit (raw data has no separate region field)
    vendor_id               BIGINT REFERENCES mis.vendor(id),
    scheduled_distance_km   NUMERIC(8, 3),        -- AVG(planned_km) across trips on this route grouping
    shift_type               TEXT,                 -- modal shift bucket (morning/afternoon/evening/night) derived from the HH in shift_type, informational only -- no sense-layer detector filters on this value
    status                   TEXT NOT NULL DEFAULT 'active',
    created_at                TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_mis_route_org_id ON mis.route(org_id);
CREATE INDEX idx_mis_route_vendor_id ON mis.route(vendor_id);

-- ---------------------------------------------------------------------------
-- mis.driver -- NO real driver identity exists in the anonymised data, only
-- per-trip is_driver_nc/is_cab_nc compliance flags and a cab registration
-- plate. This table is a best-effort "cab compliance unit" keyed by
-- (vendor_id, actual_cab_registration) -- it is a proxy for a driver, not a
-- real driver record. full_name/license_number/phone are intentionally left
-- NULL rather than fabricated; license_number being NULL on every row is
-- expected and is exactly what flag_data_quality's existing null-license
-- check in sense/nodes.py is designed to catch (a handful of thousand real,
-- meaningful flags -- not invented ones).
-- ---------------------------------------------------------------------------
CREATE TABLE mis.driver (
    id                    BIGSERIAL PRIMARY KEY,
    org_id                TEXT NOT NULL,
    vendor_id             BIGINT REFERENCES mis.vendor(id),
    driver_code           TEXT NOT NULL,          -- = actual_cab_registration
    full_name             TEXT,                    -- NULL: no real driver identity in the anonymised data
    license_number        TEXT,                     -- NULL: not present in the raw data (see comment above)
    phone                  TEXT,
    cab_fuel_type_mode      TEXT,                    -- modal actual_cab_fuel_type observed for this cab unit; informational
    status                  TEXT NOT NULL DEFAULT 'active', -- 'suspended' if the majority of this cab's trips were flagged is_driver_nc/is_cab_nc, else 'active'
    created_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (vendor_id, driver_code)
);
CREATE INDEX idx_mis_driver_org_id ON mis.driver(org_id);
CREATE INDEX idx_mis_driver_vendor_id ON mis.driver(vendor_id);

-- ---------------------------------------------------------------------------
-- mis.team / mis.employee -- **SYNTHETIC ENRICHMENT, NOT SOURCED FROM
-- MOVEINSYNC.** The raw data has no team or line-manager hierarchy at all
-- (only an anonymised `stwid` rider id, gender, and emp_role). The Line
-- Manager persona needs *some* team/manager structure to scope its
-- dashboards and Q&A, so employees are grouped into teams of ~20 by
-- (business_unit, most-common office), and one synthetic manager "employee"
-- row is created per team (id space disjoint from real stwids -- see
-- transform.sql) and wired up as that team's line_manager_id and as every
-- real employee-in-that-team's line_manager_id. This is a plausible
-- placeholder org chart layered on top of real trip/rider data -- it is
-- NOT MoveInSync's actual reporting structure and must not be presented as
-- such to a judge or end user.
-- ---------------------------------------------------------------------------
CREATE TABLE mis.team (
    id                BIGSERIAL PRIMARY KEY,
    org_id            TEXT NOT NULL,
    name              TEXT NOT NULL,
    region            TEXT NOT NULL,
    line_manager_id   BIGINT,                     -- FK to mis.employee added below, after mis.employee exists (mutually referential, same pattern as backend/db/schema.sql)
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_mis_team_org_id ON mis.team(org_id);

CREATE TABLE mis.employee (
    id                BIGINT PRIMARY KEY,          -- real employees: = stwid (already a unique natural key, stwid=0 placeholder excluded); synthetic managers: negative ids disjoint from the real stwid space, see transform.sql
    org_id            TEXT NOT NULL,
    employee_code     TEXT NOT NULL,
    full_name         TEXT,                        -- NULL for real riders: no name in the anonymised data, not fabricated. Populated with a "Team N Manager" label ONLY for the synthetic manager rows.
    email             TEXT,                         -- SYNTHETIC placeholder ("stw<id>@<org>.mis-demo.internal"), not sourced -- see backend/db/real_data/README.md for why this one field is synthesized rather than left NULL+flagged
    gender            TEXT,                         -- modal non-null gender observed for this stwid across emp_data rows
    emp_role          TEXT,                         -- modal non-null emp_role observed for this stwid
    team_id           BIGINT REFERENCES mis.team(id),
    line_manager_id   BIGINT REFERENCES mis.employee(id),
    home_location     TEXT,                          -- modal office
    work_location     TEXT,
    status            TEXT NOT NULL DEFAULT 'active',
    is_synthetic_manager BOOLEAN NOT NULL DEFAULT FALSE, -- TRUE only for the fabricated per-team manager rows described above
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (org_id, employee_code)
);
CREATE INDEX idx_mis_employee_org_id ON mis.employee(org_id);
CREATE INDEX idx_mis_employee_team_id ON mis.employee(team_id);
CREATE INDEX idx_mis_employee_line_manager_id ON mis.employee(line_manager_id);

ALTER TABLE mis.team
    ADD CONSTRAINT fk_mis_team_line_manager FOREIGN KEY (line_manager_id) REFERENCES mis.employee(id);

-- ---------------------------------------------------------------------------
-- mis.trip -- the trip spine, from the three ride_data_trip monthly CSVs
-- unioned. org_id = business_unit (5 real orgs -- the genuine multi-tenant
-- win called out in the task). id = the raw trip_id itself (normalised to
-- bigint).
--
-- trip_id is ALMOST globally unique across the three months but not quite:
-- ~6,754 trip_id values (~1.1%) collide across two different
-- (business_unit, month) pairs -- an undocumented quirk found during
-- ingestion, not one of the Dictionary's 9. Every collision observed
-- crosses business_unit boundaries. Each collision is logged into
-- data_quality_flags (issue_type='duplicate_row', severity='high') in
-- transform.sql; mis.trip keeps only the later-completing trip
-- (DISTINCT ON ... ORDER BY actual_end_epoch DESC) since it needs exactly
-- one row per id. This means the small number of bill_data/alerts_data/
-- trip_feedback/emp_data rows referencing a colliding trip_id may join
-- against the OTHER business_unit's trip -- a disclosed limitation (see
-- backend/db/real_data/README.md), not silently resolved.
-- ---------------------------------------------------------------------------
CREATE TABLE mis.trip (
    id                    BIGINT PRIMARY KEY,       -- normalised trip_id
    org_id                TEXT NOT NULL,
    route_id              BIGINT REFERENCES mis.route(id),
    driver_id             BIGINT REFERENCES mis.driver(id),
    trip_date              DATE NOT NULL,
    scheduled_departure    TIMESTAMPTZ,               -- planned_start_epoch
    scheduled_arrival       TIMESTAMPTZ,               -- planned_end_epoch (contract logical name: scheduled_time)
    actual_departure         TIMESTAMPTZ,               -- actual_start_epoch
    actual_arrival             TIMESTAMPTZ,               -- actual_end_epoch (contract logical name: actual_time)
    passenger_count             INTEGER NOT NULL DEFAULT 0, -- actualemployee_cnt
    status                       TEXT NOT NULL DEFAULT 'completed', -- 'completed' when both actual_* epochs present, 'cancelled' when both absent, else 'in_progress'
    -- columns kept close to the raw file, beyond what the contract requires,
    -- for traceability and for the emission/route/driver derivations below
    business_unit                 TEXT NOT NULL,
    office                         TEXT NOT NULL,
    product_type                    TEXT NOT NULL,
    trip_direction                   TEXT NOT NULL CHECK (trip_direction IN ('LOGIN', 'LOGOUT')),
    vendor_id                        BIGINT REFERENCES mis.vendor(id),
    planned_cab_registration          TEXT,
    actual_cab_registration            TEXT,
    actual_cab_capacity                 INTEGER,
    planned_km                           NUMERIC(10, 3),
    traveled_km                           NUMERIC(10, 3),
    delay_reason                           TEXT,
    delay_minutes                           NUMERIC(10, 2),
    route_source                             TEXT,
    actual_cab_fuel_type                      TEXT,
    is_driver_nc                               BOOLEAN,
    is_cab_nc                                   BOOLEAN,
    trip_nodal                                   TEXT,
    plannedemployee_cnt                           INTEGER,
    noshow_cnt                                     INTEGER,
    source_month                                    TEXT NOT NULL,
    created_at                                       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_mis_trip_org_id ON mis.trip(org_id);
CREATE INDEX idx_mis_trip_route_id ON mis.trip(route_id);
CREATE INDEX idx_mis_trip_driver_id ON mis.trip(driver_id);
CREATE INDEX idx_mis_trip_trip_date ON mis.trip(trip_date);
CREATE INDEX idx_mis_trip_scheduled_arrival ON mis.trip(scheduled_arrival);
CREATE INDEX idx_mis_trip_status ON mis.trip(status);

-- ---------------------------------------------------------------------------
-- mis.commute -- the primary employee-leg fact table, from emp_data (the
-- cleanest join-key file per its own dictionary). trip_id/route_id are
-- resolved via LEFT JOIN to mis.trip and left NULL (with an
-- 'orphaned_reference' data_quality_flags row) when unresolved -- no hard
-- FK, see the module-level comment on cross-file trip_id links.
-- ---------------------------------------------------------------------------
CREATE TABLE mis.commute (
    id                    BIGSERIAL PRIMARY KEY,
    org_id                TEXT NOT NULL,
    employee_id           BIGINT NOT NULL REFERENCES mis.employee(id), -- stwid=0 placeholder rows are excluded entirely, not inserted (see README)
    trip_id               BIGINT,                 -- soft reference to mis.trip(id), not FK-enforced (see module comment)
    route_id              BIGINT REFERENCES mis.route(id),
    log_date              DATE NOT NULL,
    boarding_time          TIMESTAMPTZ,             -- actual_pickup_epoch
    alighting_time          TIMESTAMPTZ,             -- actual_drop_epoch
    mode                     TEXT NOT NULL,           -- 'shuttle' for BUS legs, 'cab' for CAB/SPOT_2.0 -- this mapping matters: detect_attendance_correlation in sense/nodes.py filters on mode='shuttle'
    status                    TEXT NOT NULL,           -- 'completed' (boarded) / 'missed' (not boarded)
    planned_km                 NUMERIC(10, 3),          -- NULL + flagged if negative in the raw data
    traveled_km                 NUMERIC(10, 3),          -- NULL + flagged if negative in the raw data
    shift_type                   TEXT,
    signintype                    TEXT,
    boarding_status                TEXT,
    not_boarding_reason              TEXT,             -- NON_COMMUNICATING / TRIP_CANCELLED_FROM_DASHBOARD / NO_SHOW -- kept verbatim so downstream reasoning can distinguish real no-show causes
    is_no_show                        BOOLEAN,
    created_at                          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_mis_commute_org_id ON mis.commute(org_id);
CREATE INDEX idx_mis_commute_employee_id ON mis.commute(employee_id);
CREATE INDEX idx_mis_commute_trip_id ON mis.commute(trip_id);
CREATE INDEX idx_mis_commute_log_date ON mis.commute(log_date);

-- ---------------------------------------------------------------------------
-- mis.attendance -- REPURPOSED as commute-derived attendance: there is no
-- office clock-in/out data anywhere in the raw dataset. Built ONLY from each
-- employee's LOGIN-direction leg per work_date (join mis.commute -> mis.trip
-- ON trip_direction = 'LOGIN'): late_minutes = actual pickup - planned
-- pickup, status = 'late' when that exceeds a 10-minute tolerance. This
-- means "attendance" and "commute delay" are correlated *by construction*
-- for LOGIN legs -- flagged loudly here and in the ingestion report, not
-- hidden. detect_attendance_correlation (sense/nodes.py, untouched) still
-- gets a meaningful signal because it further splits on mode='shuttle' vs
-- delay magnitude, and not_boarding_reason on the source commute row lets
-- future reasoning separate transport-caused misses from
-- NON_COMMUNICATING/TRIP_CANCELLED_FROM_DASHBOARD ones.
-- ---------------------------------------------------------------------------
CREATE TABLE mis.attendance (
    id                BIGSERIAL PRIMARY KEY,
    org_id            TEXT NOT NULL,
    employee_id       BIGINT NOT NULL REFERENCES mis.employee(id),
    work_date         DATE NOT NULL,
    clock_in_time     TIMESTAMPTZ,                -- actual_pickup_epoch of the LOGIN leg (a commute proxy, not a real HR clock-in)
    clock_out_time    TIMESTAMPTZ,                -- always NULL: no real clock-out/HR data exists in the raw dataset
    status            TEXT NOT NULL DEFAULT 'present', -- 'late' / 'present' / 'absent' (see comment above)
    late_minutes      INTEGER NOT NULL DEFAULT 0,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (employee_id, work_date)
);
CREATE INDEX idx_mis_attendance_org_id ON mis.attendance(org_id);
CREATE INDEX idx_mis_attendance_employee_id ON mis.attendance(employee_id);
CREATE INDEX idx_mis_attendance_work_date ON mis.attendance(work_date);

-- ---------------------------------------------------------------------------
-- mis.incident -- from alerts_data. severity is remapped from the raw
-- Sev-1/Sev-2/Sev-3 scale to the low/medium/high/critical vocabulary that
-- sense/nodes.py's INCIDENT_SEVERITY_RANK dict expects (a VALUE-domain
-- mismatch, not a naming one -- the column name maps fine through the
-- contract, but the raw values would silently rank as unknown/lowest
-- without this translation). MoveInSync severity conventionally runs most-
-- severe-first, so Sev-1 -> critical, Sev-2 -> high, Sev-3 -> medium; the
-- stray literal "False" and true nulls both map to NULL severity (flagged).
-- route_id/driver_id are backfilled via trip_id -> mis.trip, not present in
-- alerts_data itself.
-- ---------------------------------------------------------------------------
CREATE TABLE mis.incident (
    id             TEXT PRIMARY KEY,               -- = event_id (uuid string, already unique)
    org_id         TEXT NOT NULL,
    trip_id        BIGINT,                          -- soft reference to mis.trip(id)
    route_id       BIGINT REFERENCES mis.route(id),  -- backfilled via trip_id -> mis.trip.route_id
    driver_id      BIGINT REFERENCES mis.driver(id), -- backfilled via trip_id -> mis.trip.driver_id
    stwid          BIGINT,                           -- 0 placeholder kept as NULL (trip-level alert, not employee-specific)
    incident_type  TEXT NOT NULL,                     -- = event_type
    severity       TEXT,                               -- low/medium/high/critical, remapped from Sev-1/2/3 (see comment above); NULL when raw value was "False" or genuinely null
    description    TEXT,                                -- synthesized short text from source/state_text, not a fabricated narrative
    occurred_at    TIMESTAMPTZ NOT NULL,                 -- start_time
    reported_at    TIMESTAMPTZ NOT NULL,                  -- = occurred_at: raw data has no separate report timestamp
    status         TEXT NOT NULL DEFAULT 'open',           -- state_text NEW/OPEN -> 'open', CLOSED -> 'resolved'
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_mis_incident_org_id ON mis.incident(org_id);
CREATE INDEX idx_mis_incident_trip_id ON mis.incident(trip_id);
CREATE INDEX idx_mis_incident_route_id ON mis.incident(route_id);
CREATE INDEX idx_mis_incident_occurred_at ON mis.incident(occurred_at);
CREATE INDEX idx_mis_incident_status ON mis.incident(status);

-- ---------------------------------------------------------------------------
-- mis.cost -- from bill_data. cost_date prefers the matched trip's real
-- trip_date (via trip_id -> mis.trip) and falls back to the billing cycle's
-- start date only when the trip_id doesn't resolve, since bill_data itself
-- carries no per-trip date, only a semi-monthly cycle window.
-- cost_per_km is guarded against total_trip_km <= 0.1km (a documented,
-- meaningful share of bill_data rows are exactly 0; near-zero values are
-- folded into the same guard since dividing real cost by e.g. 0.0003km
-- produces a multi-million-INR/km "rate" that isn't a meaningful number,
-- just an artifact of the raw distance value being wrong) -- set NULL and
-- flagged rather than producing inf or an absurd outlier. When billing
-- distance is unusable this way, cost_per_km_inr falls back to the same
-- trip_id's mis.trip.traveled_km/planned_km (real telemetry from the
-- ride_data_trip file) rather than giving up -- vanta-Aus and vanta-Sea's
-- vendors bill almost entirely by distance *slab* (total_trip_km is a
-- literal 0 for ~97-99.97% of their bill_data rows; the other three orgs are
-- 99%+ populated), and without this fallback their vendors/cost rows were
-- left with no cost_per_km_inr at all even though the trip's real distance
-- is known. distance_km itself is NOT affected by this fallback -- it stays
-- the raw billed total_trip_km verbatim (chart_data.billing_discrepancy
-- deliberately diffs distance_km against traveled_km, so it must keep
-- meaning "what was billed", not "what was driven"). A second, UNDOCUMENTED
-- quirk found during ingestion: 189 bill_data rows carry a NEGATIVE
-- trip_cost (some under a literal trip_id="OverHead" -- a billing
-- correction/adjustment line, not a per-trip fare); these are flagged and
-- excluded from this table entirely, not inserted with a clipped value,
-- since a billing adjustment has no valid per-trip substitute. A third:
-- a handful of rows per org (up to 585 in vanta-Sea) have a valid
-- total_trip_km but a $0 trip_cost -- the row is kept (it's not a billing
-- adjustment), but cost_per_km_inr is guarded to NULL for the same
-- divide-by-a-meaningless-number reason as the distance guard.
-- ---------------------------------------------------------------------------
CREATE TABLE mis.cost (
    id               BIGSERIAL PRIMARY KEY,
    org_id           TEXT NOT NULL,
    route_id         BIGINT REFERENCES mis.route(id),   -- backfilled via trip_id -> mis.trip.route_id
    vendor_id        BIGINT REFERENCES mis.vendor(id),
    trip_id          BIGINT,                              -- soft reference to mis.trip(id)
    cost_date        DATE NOT NULL,
    distance_km      NUMERIC(10, 3),                        -- total_trip_km
    passenger_count  INTEGER,                                -- backfilled from mis.trip when resolved
    total_cost_inr   NUMERIC(12, 2) NOT NULL,                 -- cleaned trip_cost
    cost_per_km_inr  NUMERIC(12, 4),                           -- amount / best usable distance (billed, else trip's traveled/planned km); NULL when neither is usable or amount is 0 (flagged -- see mis.cost comment)
    cost_category    TEXT NOT NULL DEFAULT 'trip_fare',
    contract         TEXT,
    slab_name        TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_mis_cost_org_id ON mis.cost(org_id);
CREATE INDEX idx_mis_cost_route_id ON mis.cost(route_id);
CREATE INDEX idx_mis_cost_vendor_id ON mis.cost(vendor_id);
CREATE INDEX idx_mis_cost_trip_id ON mis.cost(trip_id);
CREATE INDEX idx_mis_cost_cost_date ON mis.cost(cost_date);

-- ---------------------------------------------------------------------------
-- mis.emission -- NOT present in the raw data at all; fully derived, one row
-- per mis.trip with a resolved fuel type and positive passenger_count.
-- co2_grams = traveled_km (or planned_km if traveled_km is unavailable) x a
-- per-km vehicle emission factor keyed by actual_cab_fuel_type. Set to the
-- explicit values docs/moveinsync-prd-v3.md (Feature 5) specifies -- a
-- simple tailpipe-only model, not well-to-wheel:
--   Diesel   170 gCO2/km
--   Petrol   150 gCO2/km
--   Electric   0 gCO2/km
-- (An earlier pass here used a grid-average *indirect* estimate for Electric
-- (~130 g/km, via India's CEA baseline grid factor) on the reasoning that a
-- literal 0 flatters EVs vs. their real well-to-wheel footprint -- that
-- nuance is genuine, but the PRD's explicit tailpipe-only numbers are now
-- the authoritative spec this build follows.)
-- co2_per_passenger_km = the vehicle-level per-km factor divided by
-- passenger_count -- the standard shared-mobility carbon-accounting move
-- (spread one vehicle's emissions across its occupants for that km), and the
-- unit the existing 82 gCO2/passenger-km ICE baseline in
-- sustainability_targets is already expressed in.
-- ---------------------------------------------------------------------------
CREATE TABLE mis.emission (
    id                    BIGSERIAL PRIMARY KEY,
    org_id                TEXT NOT NULL,
    route_id              BIGINT REFERENCES mis.route(id),
    trip_id               BIGINT,                        -- soft reference to mis.trip(id) (always resolves in practice: emission rows are derived FROM mis.trip)
    log_date              DATE NOT NULL,
    distance_km           NUMERIC(10, 3),
    passenger_count       INTEGER NOT NULL,
    co2_grams             NUMERIC(12, 2) NOT NULL,
    co2_per_passenger_km  NUMERIC(10, 3),
    vehicle_type          TEXT NOT NULL,                  -- 'EV' for Electric, 'ICE' for Diesel/Petrol
    fuel_type              TEXT NOT NULL,                  -- raw actual_cab_fuel_type, kept for traceability of the factor used
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_mis_emission_org_id ON mis.emission(org_id);
CREATE INDEX idx_mis_emission_route_id ON mis.emission(route_id);
CREATE INDEX idx_mis_emission_log_date ON mis.emission(log_date);

-- ---------------------------------------------------------------------------
-- mis.feedback -- NEW entity, not in the original synthetic contract at all.
-- Purely additive: route/driver/cab/safety/marshal ratings from
-- trip_feedback, doesn't change the shape of any existing entity.
-- ---------------------------------------------------------------------------
CREATE TABLE mis.feedback (
    id               BIGSERIAL PRIMARY KEY,
    org_id           TEXT NOT NULL,
    trip_id          BIGINT,                       -- soft reference to mis.trip(id)
    employee_id      BIGINT,                        -- soft reference to mis.employee(id); NULL when stwid was 0 or unresolved
    trip_type        TEXT NOT NULL,                 -- LOGIN / LOGOUT
    trip_date        TIMESTAMPTZ,
    route_rating     SMALLINT,
    driver_rating    SMALLINT,
    cab_rating       SMALLINT,
    safety_rating    SMALLINT,
    marshal_rating   SMALLINT,
    creation_time    TIMESTAMPTZ,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_mis_feedback_org_id ON mis.feedback(org_id);
CREATE INDEX idx_mis_feedback_trip_id ON mis.feedback(trip_id);
CREATE INDEX idx_mis_feedback_employee_id ON mis.feedback(employee_id);
