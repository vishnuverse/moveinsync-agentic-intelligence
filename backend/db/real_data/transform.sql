-- Transform stg.* (raw text, loaded by ingest.py via COPY) into mis.* (typed,
-- cleaned, dimensionally-modeled). Executed by ingest.py, which splits this
-- file on the "-- STEP:" markers and runs + times + logs the row count of
-- each step in order -- keep every step idempotent-safe (mis schema is
-- dropped/recreated by mis_schema.sql before this file runs, so plain
-- INSERTs are fine, no upsert logic needed).
--
-- Parsing philosophy: Postgres's to_date/to_timestamp/::numeric/::bigint all
-- RAISE on a malformed value, which would abort an entire bulk INSERT over
-- one bad row -- unacceptable for real, messy data. The stg.safe_* helper
-- functions below wrap each conversion in an exception handler that returns
-- NULL instead, so a single unparseable row degrades to a logged data-quality
-- flag (see the flag_* steps) rather than crashing the whole load.

-- STEP: helper functions
CREATE OR REPLACE FUNCTION stg.safe_numeric(raw TEXT) RETURNS NUMERIC AS $$
BEGIN
    IF raw IS NULL OR trim(raw) = '' THEN RETURN NULL; END IF;
    RETURN regexp_replace(trim(raw), ',', '', 'g')::numeric;
EXCEPTION WHEN OTHERS THEN RETURN NULL;
END;
$$ LANGUAGE plpgsql IMMUTABLE;

CREATE OR REPLACE FUNCTION stg.safe_bigint(raw TEXT) RETURNS BIGINT AS $$
BEGIN
    IF raw IS NULL OR trim(raw) = '' THEN RETURN NULL; END IF;
    RETURN regexp_replace(trim(raw), ',', '', 'g')::numeric::bigint;
EXCEPTION WHEN OTHERS THEN RETURN NULL;
END;
$$ LANGUAGE plpgsql IMMUTABLE;

CREATE OR REPLACE FUNCTION stg.safe_int(raw TEXT) RETURNS INTEGER AS $$
BEGIN
    IF raw IS NULL OR trim(raw) = '' THEN RETURN NULL; END IF;
    RETURN regexp_replace(trim(raw), ',', '', 'g')::numeric::int;
EXCEPTION WHEN OTHERS THEN RETURN NULL;
END;
$$ LANGUAGE plpgsql IMMUTABLE;

CREATE OR REPLACE FUNCTION stg.safe_bool(raw TEXT) RETURNS BOOLEAN AS $$
BEGIN
    IF raw IS NULL OR trim(raw) = '' THEN RETURN NULL; END IF;
    IF lower(trim(raw)) IN ('true', 't', '1') THEN RETURN TRUE; END IF;
    IF lower(trim(raw)) IN ('false', 'f', '0') THEN RETURN FALSE; END IF;
    RETURN NULL;
EXCEPTION WHEN OTHERS THEN RETURN NULL;
END;
$$ LANGUAGE plpgsql IMMUTABLE;

-- 'May 1, 2026' -> date
CREATE OR REPLACE FUNCTION stg.safe_date_mdy(raw TEXT) RETURNS DATE AS $$
BEGIN
    IF raw IS NULL OR trim(raw) = '' THEN RETURN NULL; END IF;
    RETURN to_date(trim(raw), 'FMMonth FMDD, YYYY');
EXCEPTION WHEN OTHERS THEN RETURN NULL;
END;
$$ LANGUAGE plpgsql IMMUTABLE;

-- 'June 3, 2026, 11:00 AM' -> timestamptz
CREATE OR REPLACE FUNCTION stg.safe_ts_mdy_hm(raw TEXT) RETURNS TIMESTAMPTZ AS $$
BEGIN
    IF raw IS NULL OR trim(raw) = '' THEN RETURN NULL; END IF;
    RETURN to_timestamp(trim(raw), 'FMMonth FMDD, YYYY, HH12:MI AM');
EXCEPTION WHEN OTHERS THEN RETURN NULL;
END;
$$ LANGUAGE plpgsql IMMUTABLE;

-- '2026-07-09' -> date
CREATE OR REPLACE FUNCTION stg.safe_date_iso(raw TEXT) RETURNS DATE AS $$
BEGIN
    IF raw IS NULL OR trim(raw) = '' THEN RETURN NULL; END IF;
    RETURN to_date(trim(raw), 'YYYY-MM-DD');
EXCEPTION WHEN OTHERS THEN RETURN NULL;
END;
$$ LANGUAGE plpgsql IMMUTABLE;

-- comma-formatted or float-string unix epoch seconds -> timestamptz
CREATE OR REPLACE FUNCTION stg.safe_epoch_ts(raw TEXT) RETURNS TIMESTAMPTZ AS $$
DECLARE
    cleaned NUMERIC;
BEGIN
    IF raw IS NULL OR trim(raw) = '' THEN RETURN NULL; END IF;
    cleaned := regexp_replace(trim(raw), ',', '', 'g')::numeric;
    RETURN to_timestamp(cleaned);
EXCEPTION WHEN OTHERS THEN RETURN NULL;
END;
$$ LANGUAGE plpgsql IMMUTABLE;

-- STEP: clear this pipeline's own prior data_quality_flags rows
-- ---------------------------------------------------------------------------
-- public.data_quality_flags is shared infra (see mis_schema.sql's module
-- comment) and deliberately NOT part of the stg/mis DROP SCHEMA ... CASCADE
-- reset, so a re-run must clear out its OWN previous ingest-time rows first
-- or they silently double up on every re-run. Scoped to the 5 raw source
-- filenames this pipeline writes under -- flag_data_quality (the live
-- sense-layer node, unmodified) writes contract-resolved table names like
-- 'mis.trip'/'mis.employee'/'mis.driver' as its source_table, which never
-- collide with these, so this DELETE cannot touch its rows.
DELETE FROM data_quality_flags
WHERE source_table IN ('ride_data_trip', 'emp_data', 'bill_data', 'alerts_data', 'trip_feedback');

-- ---------------------------------------------------------------------------
-- STEP: flag unparseable trip_date / trip_id in ride_data_trip
-- ---------------------------------------------------------------------------
INSERT INTO data_quality_flags (org_id, source_table, source_pk, issue_type, issue_detail, severity)
SELECT business_unit, 'ride_data_trip', COALESCE(trip_id, '(null)'), 'malformed_timestamp',
       'trip_date "' || trip_date || '" did not parse as "Month D, YYYY"', 'medium'
FROM stg.ride_trip
WHERE stg.safe_date_mdy(trip_date) IS NULL;

INSERT INTO data_quality_flags (org_id, source_table, source_pk, issue_type, issue_detail, severity)
SELECT business_unit, 'ride_data_trip', COALESCE(trip_id, '(null)'), 'other',
       'trip_id "' || trip_id || '" did not normalise to an integer', 'high'
FROM stg.ride_trip
WHERE stg.safe_bigint(trip_id) IS NULL;

-- ---------------------------------------------------------------------------
-- STEP: vendor -- reconcile ride_data_trip.vendor_id and bill_data.vendor by
-- case/whitespace-normalised name (see mis_schema.sql comment on mis.vendor)
-- ---------------------------------------------------------------------------
CREATE TEMP TABLE vendor_candidates AS
SELECT norm_name, raw_name, org_id, sum(cnt) AS total_cnt FROM (
    SELECT lower(trim(vendor_id)) AS norm_name, trim(vendor_id) AS raw_name, business_unit AS org_id, count(*) AS cnt
    FROM stg.ride_trip WHERE vendor_id IS NOT NULL AND trim(vendor_id) <> ''
    GROUP BY 1, 2, 3
    UNION ALL
    SELECT lower(trim(vendor)) AS norm_name, trim(vendor) AS raw_name, business_unit AS org_id, count(*) AS cnt
    FROM stg.bill_data WHERE vendor IS NOT NULL AND trim(vendor) <> ''
    GROUP BY 1, 2, 3
) u
GROUP BY norm_name, raw_name, org_id;

CREATE TEMP TABLE vendor_dominant_org AS
SELECT DISTINCT ON (norm_name) norm_name, raw_name, org_id
FROM vendor_candidates
ORDER BY norm_name, total_cnt DESC;

INSERT INTO mis.vendor (org_id, name, norm_name)
SELECT org_id, raw_name, norm_name FROM vendor_dominant_org;

-- ---------------------------------------------------------------------------
-- STEP: route -- grain = distinct (business_unit, office, product_type)
-- ---------------------------------------------------------------------------
CREATE TEMP TABLE route_groups AS
SELECT business_unit, office, product_type,
       avg(stg.safe_numeric(planned_km)) AS avg_planned_km
FROM stg.ride_trip
GROUP BY business_unit, office, product_type;

CREATE TEMP TABLE route_modal_vendor AS
SELECT business_unit, office, product_type, norm_name FROM (
    SELECT business_unit, office, product_type, lower(trim(vendor_id)) AS norm_name, count(*) AS cnt,
           row_number() OVER (PARTITION BY business_unit, office, product_type ORDER BY count(*) DESC) AS rn
    FROM stg.ride_trip
    WHERE vendor_id IS NOT NULL AND trim(vendor_id) <> ''
    GROUP BY business_unit, office, product_type, lower(trim(vendor_id))
) x WHERE rn = 1;

CREATE TEMP TABLE route_modal_shift AS
SELECT business_unit, office, product_type, shift_bucket FROM (
    SELECT business_unit, office, product_type, shift_bucket, count(*) AS cnt,
           row_number() OVER (PARTITION BY business_unit, office, product_type ORDER BY count(*) DESC) AS rn
    FROM (
        SELECT business_unit, office, product_type,
            CASE
                WHEN split_part(shift_type, ':', 1) ~ '^[0-9]+$' AND split_part(shift_type, ':', 1)::int BETWEEN 5 AND 11 THEN 'morning'
                WHEN split_part(shift_type, ':', 1) ~ '^[0-9]+$' AND split_part(shift_type, ':', 1)::int BETWEEN 12 AND 16 THEN 'afternoon'
                WHEN split_part(shift_type, ':', 1) ~ '^[0-9]+$' AND split_part(shift_type, ':', 1)::int BETWEEN 17 AND 20 THEN 'evening'
                ELSE 'night'
            END AS shift_bucket
        FROM stg.ride_trip
    ) s
    GROUP BY business_unit, office, product_type, shift_bucket
) y WHERE rn = 1;

INSERT INTO mis.route (org_id, route_code, name, origin, destination, region, vendor_id, scheduled_distance_km, shift_type, status)
SELECT
    g.business_unit,
    lower(regexp_replace(g.business_unit || '-' || g.office || '-' || g.product_type, '[^a-zA-Z0-9]+', '-', 'g')),
    g.office || ' ' || g.product_type,
    g.office,
    'Employee commute',
    g.business_unit,
    v.id,
    round(g.avg_planned_km, 3),
    ms.shift_bucket,
    'active'
FROM route_groups g
LEFT JOIN route_modal_vendor mv ON mv.business_unit = g.business_unit AND mv.office = g.office AND mv.product_type = g.product_type
LEFT JOIN mis.vendor v ON v.norm_name = mv.norm_name
LEFT JOIN route_modal_shift ms ON ms.business_unit = g.business_unit AND ms.office = g.office AND ms.product_type = g.product_type;

-- ---------------------------------------------------------------------------
-- STEP: driver -- grain = (vendor, actual_cab_registration); a proxy for
-- driver identity, see mis_schema.sql comment on mis.driver
-- ---------------------------------------------------------------------------
CREATE TEMP TABLE cab_groups AS
SELECT lower(trim(vendor_id)) AS norm_name, business_unit, trim(actual_cab_registration) AS driver_code,
       count(*) FILTER (WHERE stg.safe_bool(is_driver_nc) IS TRUE OR stg.safe_bool(is_cab_nc) IS TRUE) AS nc_count,
       count(*) AS total_count,
       mode() WITHIN GROUP (ORDER BY actual_cab_fuel_type) AS fuel_mode
FROM stg.ride_trip
WHERE actual_cab_registration IS NOT NULL AND trim(actual_cab_registration) <> ''
  AND vendor_id IS NOT NULL AND trim(vendor_id) <> ''
GROUP BY 1, 2, 3;

CREATE TEMP TABLE cab_groups_ranked AS
SELECT *, row_number() OVER (PARTITION BY norm_name, driver_code ORDER BY total_count DESC) AS rn
FROM cab_groups;

INSERT INTO mis.driver (org_id, vendor_id, driver_code, cab_fuel_type_mode, status)
SELECT r.business_unit, v.id, r.driver_code, r.fuel_mode,
       CASE WHEN r.total_count > 0 AND r.nc_count::numeric / r.total_count > 0.5 THEN 'suspended' ELSE 'active' END
FROM cab_groups_ranked r
JOIN mis.vendor v ON v.norm_name = r.norm_name
WHERE r.rn = 1;

-- ---------------------------------------------------------------------------
-- STEP: synthetic team + employee hierarchy (SEE mis_schema.sql -- this
-- entire step is fabricated org structure, not sourced from MoveInSync)
-- ---------------------------------------------------------------------------
CREATE TEMP TABLE emp_mode_bu AS
SELECT DISTINCT ON (stwid) stwid, business_unit FROM (
    SELECT stwid, business_unit, count(*) AS cnt FROM stg.emp_data
    WHERE stwid IS NOT NULL AND trim(stwid) NOT IN ('', '0')
    GROUP BY stwid, business_unit
) x ORDER BY stwid, cnt DESC;

CREATE TEMP TABLE emp_mode_office AS
SELECT DISTINCT ON (stwid) stwid, office FROM (
    SELECT stwid, office, count(*) AS cnt FROM stg.emp_data
    WHERE stwid IS NOT NULL AND trim(stwid) NOT IN ('', '0')
    GROUP BY stwid, office
) x ORDER BY stwid, cnt DESC;

CREATE TEMP TABLE emp_mode_gender AS
SELECT DISTINCT ON (stwid) stwid, gender FROM (
    SELECT stwid, gender, count(*) AS cnt FROM stg.emp_data
    WHERE stwid IS NOT NULL AND trim(stwid) NOT IN ('', '0') AND gender IS NOT NULL AND trim(gender) <> ''
    GROUP BY stwid, gender
) x ORDER BY stwid, cnt DESC;

CREATE TEMP TABLE emp_mode_role AS
SELECT DISTINCT ON (stwid) stwid, emp_role FROM (
    SELECT stwid, emp_role, count(*) AS cnt FROM stg.emp_data
    WHERE stwid IS NOT NULL AND trim(stwid) NOT IN ('', '0') AND emp_role IS NOT NULL AND trim(emp_role) <> ''
    GROUP BY stwid, emp_role
) x ORDER BY stwid, cnt DESC;

CREATE TEMP TABLE emp_home AS
SELECT b.stwid::bigint AS stwid, b.business_unit AS org_id, o.office AS home_office, g.gender, r.emp_role
FROM emp_mode_bu b
LEFT JOIN emp_mode_office o ON o.stwid = b.stwid
LEFT JOIN emp_mode_gender g ON g.stwid = b.stwid
LEFT JOIN emp_mode_role r ON r.stwid = b.stwid;

-- teams of ~20 employees per (org_id, home_office)
CREATE TEMP TABLE emp_team_seq AS
SELECT stwid, org_id, home_office, gender, emp_role,
       ((row_number() OVER (PARTITION BY org_id, home_office ORDER BY stwid) - 1) / 20)::int + 1 AS team_seq
FROM emp_home;

INSERT INTO mis.team (org_id, name, region)
SELECT DISTINCT org_id, org_id || ' / ' || home_office || ' / Team ' || team_seq, org_id
FROM emp_team_seq;

CREATE TEMP TABLE team_lookup AS
SELECT id AS team_id, org_id, name FROM mis.team;

-- one synthetic manager per team; id = -team_id, disjoint from the real
-- stwid space (stwid is always >= 0 in the raw data)
INSERT INTO mis.employee (id, org_id, employee_code, full_name, email, team_id, line_manager_id, status, is_synthetic_manager)
SELECT -t.team_id, t.org_id, 'MGR-' || t.team_id,
       'Line Manager (' || t.name || ')',
       'mgr' || t.team_id || '@' || lower(regexp_replace(t.org_id, '[^a-zA-Z0-9]+', '-', 'g')) || '.mis-demo.internal',
       t.team_id, NULL, 'active', true
FROM team_lookup t;

UPDATE mis.team t SET line_manager_id = -t.id;

INSERT INTO mis.employee (id, org_id, employee_code, full_name, email, gender, emp_role, team_id, line_manager_id, home_location, work_location, status, is_synthetic_manager)
SELECT e.stwid, e.org_id, e.stwid::text, NULL,
       'stw' || e.stwid || '@' || lower(regexp_replace(e.org_id, '[^a-zA-Z0-9]+', '-', 'g')) || '.mis-demo.internal',
       e.gender, e.emp_role, tl.team_id, -tl.team_id, e.home_office, e.home_office, 'active', false
FROM emp_team_seq e
JOIN team_lookup tl ON tl.org_id = e.org_id AND tl.name = e.org_id || ' / ' || e.home_office || ' / Team ' || e.team_seq;

-- ---------------------------------------------------------------------------
-- STEP: trip -- the three ride_data_trip months unioned (stg.ride_trip
-- already holds all three, tagged by source_month)
-- ---------------------------------------------------------------------------
CREATE TEMP TABLE ride_trip_clean AS
SELECT
    stg.safe_bigint(trip_id) AS tid,
    business_unit, office, product_type, trip_date, shift_type, trip_direction,
    vendor_id, planned_cab_registration, actual_cab_registration, actual_cab_capacity,
    planned_km, traveled_km, planned_start_epoch, planned_end_epoch, actual_start_epoch, actual_end_epoch,
    delay_reason, delay_minutes, route_source, actual_cab_fuel_type, is_driver_nc, is_cab_nc,
    trip_nodal, plannedemployee_cnt, actualemployee_cnt, noshow_cnt, source_month
FROM stg.ride_trip
WHERE stg.safe_bigint(trip_id) IS NOT NULL AND stg.safe_date_mdy(trip_date) IS NOT NULL;

-- Genuine, UNDOCUMENTED-by-the-Dictionary data quirk found during ingestion:
-- trip_id is not actually globally unique across the three monthly files --
-- ~6,754 trip_id values collide across two different (business_unit, month)
-- pairs (e.g. tid 1229479 is both a May vanta-Aus LOGOUT trip and an
-- unrelated July trip under a different business_unit). Every collision
-- observed during ingestion crossed business_unit boundaries, which matters
-- for a multi-tenant system -- flagged 'high', not 'low'. mis.trip can only
-- keep one row per id (see the DISTINCT ON below, tie-broken by the later
-- actual_arrival so "most recently completed" wins deterministically); the
-- other trip's row is dropped from mis.trip, and any bill_data/alerts_data/
-- trip_feedback/emp_data row referencing that same numeric trip_id will
-- join against whichever trip mis.trip kept -- which is the CORRECT trip
-- for one of the two colliding business_units and WRONG for the other. This
-- is a disclosed limitation (see backend/db/real_data/README.md), not
-- resolved further: disambiguating would require a date-proximity
-- heuristic across files that is out of proportion for ~1.1% of trips.
INSERT INTO data_quality_flags (org_id, source_table, source_pk, issue_type, issue_detail, severity)
SELECT string_agg(DISTINCT business_unit, ','), 'ride_data_trip', tid::text, 'duplicate_row',
       'trip_id collides across (business_unit, month): ' ||
       string_agg(business_unit || '/' || source_month || '/' || trip_direction, ' vs. '),
       'high'
FROM ride_trip_clean
GROUP BY tid
HAVING count(*) > 1;

INSERT INTO mis.trip (
    id, org_id, route_id, driver_id, trip_date, scheduled_departure, scheduled_arrival,
    actual_departure, actual_arrival, passenger_count, status,
    business_unit, office, product_type, trip_direction, vendor_id,
    planned_cab_registration, actual_cab_registration, actual_cab_capacity,
    planned_km, traveled_km, delay_reason, delay_minutes, route_source,
    actual_cab_fuel_type, is_driver_nc, is_cab_nc, trip_nodal,
    plannedemployee_cnt, noshow_cnt, source_month
)
SELECT DISTINCT ON (r.tid)
    r.tid, r.business_unit, rt.id, d.id,
    stg.safe_date_mdy(r.trip_date),
    stg.safe_epoch_ts(r.planned_start_epoch),
    stg.safe_epoch_ts(r.planned_end_epoch),
    stg.safe_epoch_ts(r.actual_start_epoch),
    stg.safe_epoch_ts(r.actual_end_epoch),
    COALESCE(stg.safe_int(r.actualemployee_cnt), 0),
    CASE
        WHEN stg.safe_epoch_ts(r.actual_start_epoch) IS NOT NULL AND stg.safe_epoch_ts(r.actual_end_epoch) IS NOT NULL THEN 'completed'
        WHEN stg.safe_epoch_ts(r.actual_start_epoch) IS NULL AND stg.safe_epoch_ts(r.actual_end_epoch) IS NULL THEN 'cancelled'
        ELSE 'in_progress'
    END,
    r.business_unit, r.office, r.product_type, r.trip_direction, v.id,
    NULLIF(trim(r.planned_cab_registration), ''), NULLIF(trim(r.actual_cab_registration), ''), stg.safe_int(r.actual_cab_capacity),
    stg.safe_numeric(r.planned_km), stg.safe_numeric(r.traveled_km),
    NULLIF(trim(r.delay_reason), ''), stg.safe_numeric(r.delay_minutes), NULLIF(trim(r.route_source), ''),
    NULLIF(trim(r.actual_cab_fuel_type), ''), stg.safe_bool(r.is_driver_nc), stg.safe_bool(r.is_cab_nc),
    NULLIF(trim(r.trip_nodal), ''), stg.safe_int(r.plannedemployee_cnt), stg.safe_int(r.noshow_cnt), r.source_month
FROM ride_trip_clean r
LEFT JOIN mis.route rt ON rt.route_code = lower(regexp_replace(r.business_unit || '-' || r.office || '-' || r.product_type, '[^a-zA-Z0-9]+', '-', 'g'))
LEFT JOIN mis.vendor v ON v.norm_name = lower(trim(r.vendor_id))
LEFT JOIN mis.driver d ON d.vendor_id = v.id AND d.driver_code = trim(r.actual_cab_registration)
ORDER BY r.tid, stg.safe_epoch_ts(r.actual_end_epoch) DESC NULLS LAST, r.source_month;

-- vendor reliability/cost stats, computed from the real trip/bill data now
-- that mis.trip exists (cost stats are backfilled again after mis.cost below)
UPDATE mis.vendor ven SET sla_target_pct = sub.on_time_pct
FROM (
    SELECT vendor_id,
           100.0 * count(*) FILTER (
               WHERE actual_arrival IS NOT NULL AND scheduled_arrival IS NOT NULL
                 AND EXTRACT(EPOCH FROM (actual_arrival - scheduled_arrival)) / 60.0 <= 15
           ) / NULLIF(count(*) FILTER (WHERE actual_arrival IS NOT NULL AND scheduled_arrival IS NOT NULL), 0) AS on_time_pct
    FROM mis.trip
    WHERE vendor_id IS NOT NULL
    GROUP BY vendor_id
) sub
WHERE sub.vendor_id = ven.id;

-- ---------------------------------------------------------------------------
-- STEP: flag negative planned_km/traveled_km in emp_data
-- ---------------------------------------------------------------------------
INSERT INTO data_quality_flags (org_id, source_table, source_pk, issue_type, issue_detail, severity)
SELECT business_unit, 'emp_data', trip_id || ':' || stwid, 'out_of_range_value',
       'negative planned_km/traveled_km (' || planned_km || ' / ' || traveled_km || '), clipped to NULL', 'low'
FROM stg.emp_data
WHERE stg.safe_numeric(planned_km) < 0 OR stg.safe_numeric(traveled_km) < 0;

-- ---------------------------------------------------------------------------
-- STEP: commute -- primary employee-leg fact table, from emp_data
-- ---------------------------------------------------------------------------
CREATE TEMP TABLE emp_data_clean AS
SELECT
    stg.safe_bigint(trip_id) AS tid,
    stg.safe_bigint(stwid) AS emp_id,
    business_unit, office, product_type, shift_type,
    stg.safe_date_iso(trip_date) AS work_date,
    stg.safe_epoch_ts(planned_pickup_epoch) AS planned_pickup_ts,
    stg.safe_epoch_ts(actual_pickup_epoch) AS actual_pickup_ts,
    stg.safe_epoch_ts(actual_drop_epoch) AS actual_drop_ts,
    CASE WHEN stg.safe_numeric(planned_km) < 0 THEN NULL ELSE stg.safe_numeric(planned_km) END AS planned_km_clean,
    CASE WHEN stg.safe_numeric(traveled_km) < 0 THEN NULL ELSE stg.safe_numeric(traveled_km) END AS traveled_km_clean,
    signintype, boarding_status, not_boarding_reason, stg.safe_bool(is_no_show) AS is_no_show
FROM stg.emp_data
WHERE stwid IS NOT NULL AND trim(stwid) NOT IN ('', '0');

INSERT INTO data_quality_flags (org_id, source_table, source_pk, issue_type, issue_detail, severity)
SELECT e.business_unit, 'emp_data', e.tid::text || ':' || e.emp_id::text, 'orphaned_reference',
       'trip_id has no matching ride_data_trip row', 'low'
FROM emp_data_clean e
LEFT JOIN mis.trip t ON t.id = e.tid
WHERE e.tid IS NOT NULL AND t.id IS NULL;

INSERT INTO mis.commute (
    org_id, employee_id, trip_id, route_id, log_date, boarding_time, alighting_time,
    mode, status, planned_km, traveled_km, shift_type, signintype, boarding_status,
    not_boarding_reason, is_no_show
)
SELECT
    e.business_unit, e.emp_id, e.tid, t.route_id, e.work_date, e.actual_pickup_ts, e.actual_drop_ts,
    CASE WHEN e.product_type = 'BUS' THEN 'shuttle' ELSE 'cab' END,
    CASE WHEN e.boarding_status = 'Boarded' THEN 'completed' ELSE 'missed' END,
    e.planned_km_clean, e.traveled_km_clean, e.shift_type, e.signintype, e.boarding_status,
    e.not_boarding_reason, e.is_no_show
FROM emp_data_clean e
LEFT JOIN mis.trip t ON t.id = e.tid
WHERE e.work_date IS NOT NULL;

-- ---------------------------------------------------------------------------
-- STEP: attendance -- commute-derived, LOGIN legs only (see mis_schema.sql)
-- ---------------------------------------------------------------------------
INSERT INTO mis.attendance (org_id, employee_id, work_date, clock_in_time, status, late_minutes)
SELECT DISTINCT ON (c.employee_id, c.log_date)
    c.org_id, c.employee_id, c.log_date, c.boarding_time,
    CASE
        WHEN c.status <> 'completed' THEN 'absent'
        WHEN c.boarding_time IS NOT NULL AND ec.planned_pickup_ts IS NOT NULL
             AND EXTRACT(EPOCH FROM (c.boarding_time - ec.planned_pickup_ts)) / 60.0 > 10 THEN 'late'
        ELSE 'present'
    END,
    GREATEST(0, COALESCE(ROUND(EXTRACT(EPOCH FROM (c.boarding_time - ec.planned_pickup_ts)) / 60.0), 0))::int
FROM mis.commute c
JOIN mis.trip t ON t.id = c.trip_id AND t.trip_direction = 'LOGIN'
JOIN emp_data_clean ec ON ec.tid = c.trip_id AND ec.emp_id = c.employee_id
ORDER BY c.employee_id, c.log_date, c.boarding_time NULLS LAST;

-- ---------------------------------------------------------------------------
-- STEP: incident -- from alerts_data; severity remapped (see mis_schema.sql)
-- ---------------------------------------------------------------------------
CREATE TEMP TABLE alerts_clean AS
SELECT
    event_id, business_unit,
    stg.safe_bigint(trip_id) AS tid,
    NULLIF(stg.safe_bigint(stwid), 0) AS emp_id,
    event_type,
    stg.safe_ts_mdy_hm(start_time) AS occurred_at,
    state_text,
    CASE
        WHEN severity = 'Sev-1' THEN 'critical'
        WHEN severity = 'Sev-2' THEN 'high'
        WHEN severity = 'Sev-3' THEN 'medium'
        ELSE NULL
    END AS severity_clean,
    (severity IS NOT NULL AND severity NOT IN ('Sev-1', 'Sev-2', 'Sev-3')) AS severity_was_dirty,
    source
FROM stg.alerts_data;

INSERT INTO data_quality_flags (org_id, source_table, source_pk, issue_type, issue_detail, severity)
SELECT business_unit, 'alerts_data', event_id, 'other',
       'severity value "' || (SELECT severity FROM stg.alerts_data sa WHERE sa.event_id = alerts_clean.event_id) || '" is not one of Sev-1/Sev-2/Sev-3, cleaned to NULL',
       'low'
FROM alerts_clean
WHERE severity_was_dirty;

INSERT INTO data_quality_flags (org_id, source_table, source_pk, issue_type, issue_detail, severity)
SELECT a.business_unit, 'alerts_data', a.event_id, 'orphaned_reference', 'trip_id has no matching ride_data_trip row', 'low'
FROM alerts_clean a
LEFT JOIN mis.trip t ON t.id = a.tid
WHERE a.tid IS NOT NULL AND t.id IS NULL;

INSERT INTO mis.incident (id, org_id, trip_id, route_id, driver_id, stwid, incident_type, severity, description, occurred_at, reported_at, status)
SELECT
    a.event_id, a.business_unit, a.tid, t.route_id, t.driver_id, a.emp_id, a.event_type, a.severity_clean,
    'source=' || COALESCE(a.source, 'unknown') || '; state=' || COALESCE(a.state_text, 'unknown'),
    a.occurred_at, a.occurred_at,
    CASE WHEN a.state_text = 'CLOSED' THEN 'resolved' ELSE 'open' END
FROM alerts_clean a
LEFT JOIN mis.trip t ON t.id = a.tid
WHERE a.occurred_at IS NOT NULL;

-- ---------------------------------------------------------------------------
-- STEP: cost -- from bill_data; divide-by-zero guarded (see mis_schema.sql)
-- ---------------------------------------------------------------------------
CREATE TEMP TABLE bill_clean AS
SELECT
    stg.safe_bigint(trip_id) AS tid,
    business_unit, lower(trim(vendor)) AS vendor_norm, contract, slab_name,
    stg.safe_numeric(total_trip_km) AS distance_km,
    stg.safe_numeric(trip_cost) AS amount,
    stg.safe_ts_mdy_hm(cycle_start) AS cycle_start_ts
FROM stg.bill_data;

-- Org-specific quirk found during ingestion: for vanta-Aus and (mostly)
-- vanta-Sea, bill_data.total_trip_km is a literal 0 for the overwhelming
-- majority of rows (slab_name is a distance *bucket* like "Medium"/"Long"
-- rather than a metered figure for these two orgs' vendors -- catalyst-Sac/
-- orbit-Slc/pinnacle-Slc all carry a real decimal total_trip_km on ~99%+ of
-- rows instead). Left as a pure billing-distance divide-by-zero guard, this
-- made cost_per_km_inr NULL for ~97-99.97% of these two orgs' cost rows (and,
-- via the vendor backfill below, silently starved vanta-Aus's two vendors of
-- any cost_per_km_inr at all). But the same trip_id's real telemetry distance
-- (mis.trip.traveled_km / planned_km, sourced from ride_data_trip -- a
-- completely different raw file than bill_data) IS available for ~99% of
-- these otherwise-unusable rows, so it's used as a fallback for the
-- cost_per_km_inr calculation only -- NOT for distance_km itself, which stays
-- the raw billed total_trip_km verbatim (see mis_schema.sql comment: chart_data
-- .billing_discrepancy deliberately diffs distance_km against traveled_km, so
-- distance_km must keep meaning "what was billed", not "what was driven").
INSERT INTO data_quality_flags (org_id, source_table, source_pk, issue_type, issue_detail, severity)
SELECT b.business_unit, 'bill_data', COALESCE(b.tid::text, '(unparsed)'), 'out_of_range_value',
       'total_trip_km <= 0.1 (' || b.distance_km || ') and no usable trip.traveled_km/planned_km fallback either, cost_per_km not meaningfully computable', 'low'
FROM bill_clean b
LEFT JOIN mis.trip t ON t.id = b.tid
WHERE (b.distance_km IS NULL OR b.distance_km <= 0.1)
  AND COALESCE(t.traveled_km, t.planned_km, 0) <= 0.1;

INSERT INTO data_quality_flags (org_id, source_table, source_pk, issue_type, issue_detail, severity)
SELECT b.business_unit, 'bill_data', COALESCE(b.tid::text, '(unparsed)'), 'orphaned_reference',
       'trip_id has no matching ride_data_trip row', 'low'
FROM bill_clean b
LEFT JOIN mis.trip t ON t.id = b.tid
WHERE b.tid IS NOT NULL AND t.id IS NULL;

-- Another genuine, UNDOCUMENTED-by-the-Dictionary quirk found during
-- ingestion: 189 bill_data rows carry a NEGATIVE trip_cost (some under a
-- literal trip_id = "OverHead" rather than a real trip id -- clearly a
-- billing correction/adjustment line, not a per-trip fare). Left in, these
-- corrupt vendor cost-per-km averages by orders of magnitude (a
-- -448,973 INR/km "rate" was observed on a real vendor before this guard
-- was added). Flagged and excluded from mis.cost -- a billing adjustment
-- isn't a per-trip fare, and there is no valid positive number to substitute.
INSERT INTO data_quality_flags (org_id, source_table, source_pk, issue_type, issue_detail, severity)
SELECT business_unit, 'bill_data', COALESCE(tid::text, '(OverHead/adjustment line)'), 'out_of_range_value',
       'trip_cost is negative (' || amount || '), excluded from mis.cost as a billing correction, not a per-trip fare',
       'medium'
FROM bill_clean
WHERE amount < 0;

-- A handful of rows in every org (worst: 585 in vanta-Sea) carry a real,
-- valid total_trip_km but a $0 trip_cost -- the inverse of the "$0 km" quirk
-- above. Not a billing correction (positive-or-zero, no "OverHead" marker),
-- so the row itself is kept, but a $0 fare with real distance driven is not a
-- meaningful rate either -- guarded the same way as the divide-by-zero case
-- rather than letting it silently zero out the vendor average.
INSERT INTO data_quality_flags (org_id, source_table, source_pk, issue_type, issue_detail, severity)
SELECT business_unit, 'bill_data', COALESCE(tid::text, '(unparsed)'), 'out_of_range_value',
       'trip_cost is 0 while total_trip_km (' || distance_km || ') is valid, cost_per_km not meaningfully computable', 'low'
FROM bill_clean
WHERE amount = 0 AND distance_km IS NOT NULL AND distance_km > 0.1;

INSERT INTO mis.cost (org_id, route_id, vendor_id, trip_id, cost_date, distance_km, passenger_count, total_cost_inr, cost_per_km_inr, cost_category, contract, slab_name)
SELECT
    b.business_unit, t.route_id, v.id, b.tid,
    COALESCE(t.trip_date, b.cycle_start_ts::date, CURRENT_DATE),
    b.distance_km, t.passenger_count, b.amount,
    CASE
        WHEN b.amount > 0 AND b.distance_km IS NOT NULL AND b.distance_km > 0.1
            THEN round(b.amount / b.distance_km, 4)
        WHEN b.amount > 0 AND (b.distance_km IS NULL OR b.distance_km <= 0.1)
             AND COALESCE(t.traveled_km, t.planned_km) > 0.1
            THEN round(b.amount / COALESCE(t.traveled_km, t.planned_km), 4)
        ELSE NULL
    END,
    'trip_fare', b.contract, NULLIF(b.slab_name, '')
FROM bill_clean b
LEFT JOIN mis.trip t ON t.id = b.tid
LEFT JOIN mis.vendor v ON v.norm_name = b.vendor_norm
WHERE b.amount IS NOT NULL AND b.amount >= 0;

-- backfill vendor cost_per_km_inr from real billing data (excludes the
-- total_trip_km<=0 rows automatically since cost_per_km_inr is NULL there)
UPDATE mis.vendor ven SET cost_per_km_inr = sub.avg_cost_per_km
FROM (
    SELECT vendor_id, avg(cost_per_km_inr) AS avg_cost_per_km
    FROM mis.cost WHERE vendor_id IS NOT NULL AND cost_per_km_inr IS NOT NULL
    GROUP BY vendor_id
) sub
WHERE sub.vendor_id = ven.id;

-- ---------------------------------------------------------------------------
-- STEP: emission -- fully derived, no raw source (see mis_schema.sql for the
-- cited emission factors and per-passenger-km reasoning)
-- ---------------------------------------------------------------------------
INSERT INTO mis.emission (org_id, route_id, trip_id, log_date, distance_km, passenger_count, co2_grams, co2_per_passenger_km, vehicle_type, fuel_type)
SELECT
    org_id, route_id, id, trip_date,
    COALESCE(traveled_km, planned_km) AS distance_km,
    passenger_count,
    COALESCE(traveled_km, planned_km, 0) * factor_g_per_km,
    round(factor_g_per_km / passenger_count, 3),
    CASE WHEN actual_cab_fuel_type = 'Electric' THEN 'EV' ELSE 'ICE' END,
    actual_cab_fuel_type
FROM (
    SELECT *,
        -- PRD v3 (docs/moveinsync-prd-v3.md, Feature 5) specifies these
        -- exact tailpipe coefficients -- supersedes this file's earlier
        -- grid-average Electric estimate (130 g/km); PRD treats EV as
        -- zero-tailpipe, matching how it's compared against the 82
        -- gCO2/passenger-km ICE baseline in sustainability_targets.
        CASE actual_cab_fuel_type
            WHEN 'Diesel' THEN 170.0
            WHEN 'Petrol' THEN 150.0
            WHEN 'Electric' THEN 0.0
            ELSE NULL
        END AS factor_g_per_km
    FROM mis.trip
) x
WHERE factor_g_per_km IS NOT NULL AND passenger_count > 0 AND COALESCE(traveled_km, planned_km) IS NOT NULL;

-- ---------------------------------------------------------------------------
-- STEP: feedback -- new entity, additive (see mis_schema.sql)
-- ---------------------------------------------------------------------------
CREATE TEMP TABLE feedback_clean AS
SELECT
    stg.safe_bigint(trip_id) AS tid,
    NULLIF(stg.safe_bigint(stwid), 0) AS emp_id,
    business_unit, trip_type,
    stg.safe_ts_mdy_hm(trip_date) AS trip_date_ts,
    stg.safe_int(route_rating) AS route_rating,
    stg.safe_int(driver_rating) AS driver_rating,
    stg.safe_int(cab_rating) AS cab_rating,
    stg.safe_int(safety_rating) AS safety_rating,
    stg.safe_int(marshal_rating) AS marshal_rating,
    stg.safe_ts_mdy_hm(creation_time) AS creation_time_ts
FROM stg.trip_feedback;

INSERT INTO data_quality_flags (org_id, source_table, source_pk, issue_type, issue_detail, severity)
SELECT f.business_unit, 'trip_feedback', COALESCE(f.tid::text, '(unparsed)') || ':' || COALESCE(f.emp_id::text, '0'),
       'orphaned_reference', 'trip_id has no matching ride_data_trip row', 'low'
FROM feedback_clean f
LEFT JOIN mis.trip t ON t.id = f.tid
WHERE f.tid IS NOT NULL AND t.id IS NULL;

INSERT INTO mis.feedback (org_id, trip_id, employee_id, trip_type, trip_date, route_rating, driver_rating, cab_rating, safety_rating, marshal_rating, creation_time)
SELECT f.business_unit, f.tid, e.id, f.trip_type, f.trip_date_ts,
       f.route_rating, f.driver_rating, f.cab_rating, f.safety_rating, f.marshal_rating, f.creation_time_ts
FROM feedback_clean f
LEFT JOIN mis.employee e ON e.id = f.emp_id;

-- ---------------------------------------------------------------------------
-- STEP: sustainability_targets -- seed the existing plan §8 benchmark values
-- (unchanged from the synthetic seed) once per real org_id, so
-- detect_emissions_signal/cost_anomaly context has a per-org baseline to
-- compare against instead of falling back to the hardcoded default.
-- ---------------------------------------------------------------------------
-- like data_quality_flags, this table is shared infra outside the stg/mis
-- reset -- clear this pipeline's own 5 real org_ids before reinserting so a
-- re-run doesn't duplicate rows (leaves the synthetic 'moveinsync-demo' org
-- untouched).
DELETE FROM sustainability_targets
WHERE org_id IN ('vanta-Aus', 'catalyst-Sac', 'orbit-Slc', 'vanta-Sea', 'pinnacle-Slc');

INSERT INTO sustainability_targets (org_id, metric_name, target_value, threshold_value, unit, period, notes)
SELECT org_id, 'cost_efficiency_inr_per_passenger_km', 15.00, 18.00, 'INR_per_passenger_km', 'ongoing',
       'Industry-reasonable range for corporate shuttle service is INR 12-18 per passenger-km; target_value is the midpoint, threshold_value is the upper bound above which cost efficiency is flagged.'
FROM (VALUES ('vanta-Aus'), ('catalyst-Sac'), ('orbit-Slc'), ('vanta-Sea'), ('pinnacle-Slc')) v(org_id);

INSERT INTO sustainability_targets (org_id, metric_name, target_value, threshold_value, unit, period, notes)
SELECT org_id, 'sla_timeliness_pct', 95.00, 92.00, 'percent', 'ongoing',
       '95% on-time arrival is the target; below 92% is flagged as an actionable SLA breach.'
FROM (VALUES ('vanta-Aus'), ('catalyst-Sac'), ('orbit-Slc'), ('vanta-Sea'), ('pinnacle-Slc')) v(org_id);

INSERT INTO sustainability_targets (org_id, metric_name, target_value, threshold_value, unit, period, notes)
SELECT org_id, 'carbon_gco2_per_passenger_km', 82.00, 82.00, 'gCO2_per_passenger_km', 'ongoing',
       '82 gCO2/passenger-km is the standard ICE-fleet baseline used to judge whether an emissions trend is good or bad.'
FROM (VALUES ('vanta-Aus'), ('catalyst-Sac'), ('orbit-Slc'), ('vanta-Sea'), ('pinnacle-Slc')) v(org_id);
