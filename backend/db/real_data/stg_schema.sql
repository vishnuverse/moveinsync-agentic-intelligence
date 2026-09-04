-- Staging schema for the real MoveInSync dataset (data/*.csv).
--
-- Columns are kept as TEXT and in the exact physical column order of each
-- source CSV -- COPY ... WITH (FORMAT csv, HEADER true) maps positionally,
-- not by name, so this order must stay in lockstep with the CSV headers.
-- No type coercion happens here on purpose (see backend/db/real_data/README.md):
-- messy values (comma-formatted numbers, mixed date formats, the stray
-- "False" in alerts_data.severity, dtype drift across the 3 ride-data
-- months) must all be loadable as text first; cleaning happens in
-- transform.sql where a failure can be caught and flagged into
-- data_quality_flags instead of aborting the whole bulk load.

DROP SCHEMA IF EXISTS stg CASCADE;
CREATE SCHEMA stg;

-- ride_data_trip (3 monthly files unioned into one staging table; source_month
-- is populated by ingest.py right after each file's COPY, not part of the CSV).
CREATE TABLE stg.ride_trip (
    business_unit           TEXT,
    office                   TEXT,
    product_type             TEXT,
    trip_date                TEXT,
    shift_type               TEXT,
    trip_id                  TEXT,
    trip_direction            TEXT,
    actual_escort             TEXT,
    vendor_id                 TEXT,
    planned_cab_registration  TEXT,
    actual_cab_registration   TEXT,
    actual_cab_capacity       TEXT,
    planned_km                TEXT,
    traveled_km                TEXT,
    planned_start_epoch        TEXT,
    planned_end_epoch          TEXT,
    actual_start_epoch         TEXT,
    actual_end_epoch           TEXT,
    delay_reason                TEXT,
    delay_minutes               TEXT,
    route_source                 TEXT,
    actual_cab_fuel_type          TEXT,
    is_driver_nc                   TEXT,
    is_cab_nc                       TEXT,
    trip_nodal                       TEXT,
    plannedemployee_cnt               TEXT,
    actualemployee_cnt                 TEXT,
    noshow_cnt                          TEXT,
    source_month                         TEXT
);

-- emp_data -- the cleanest join-key file per the Dictionary; still staged as
-- text for consistency with the other four tables.
CREATE TABLE stg.emp_data (
    business_unit         TEXT,
    office                  TEXT,
    product_type            TEXT,
    trip_date                TEXT,
    shift_type                TEXT,
    trip_id                    TEXT,
    planned_pickup_epoch        TEXT,
    planned_drop_epoch           TEXT,
    actual_pickup_epoch           TEXT,
    actual_drop_epoch              TEXT,
    planned_km                      TEXT,
    traveled_km                      TEXT,
    stwid                             TEXT,
    signintype                         TEXT,
    gender                               TEXT,
    emp_role                             TEXT,
    boarding_status                       TEXT,
    not_boarding_reason                    TEXT,
    is_no_show                              TEXT
);

-- bill_data
CREATE TABLE stg.bill_data (
    business_unit  TEXT,
    office           TEXT,
    vendor            TEXT,
    cycle_start        TEXT,
    cycle_end            TEXT,
    trip_id               TEXT,
    contract               TEXT,
    slab_name                TEXT,
    total_trip_km             TEXT,
    trip_cost                  TEXT
);

-- alerts_data
CREATE TABLE stg.alerts_data (
    business_unit    TEXT,
    trip_id            TEXT,
    stwid                TEXT,
    event_id              TEXT,
    event_type              TEXT,
    start_time                TEXT,
    acknowledge_time            TEXT,
    state_text                    TEXT,
    severity                        TEXT,
    source                            TEXT
);

-- trip_feedback
CREATE TABLE stg.trip_feedback (
    business_unit   TEXT,
    trip_id           TEXT,
    trip_type           TEXT,
    trip_date             TEXT,
    stwid                   TEXT,
    route_rating              TEXT,
    driver_rating               TEXT,
    cab_rating                    TEXT,
    safety_rating                   TEXT,
    marshal_rating                    TEXT,
    creation_time                       TEXT
);
