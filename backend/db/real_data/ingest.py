"""Real MoveInSync data ingestion: data/*.csv -> stg.* (bulk COPY) -> mis.*
(cleaned, typed, dimensionally modeled) in Postgres.

Idempotent and re-runnable: `stg` and `mis` are both DROP SCHEMA ... CASCADE
then recreated on every run (see stg_schema.sql / mis_schema.sql), so this
script can simply be re-run after a data refresh -- no upsert bookkeeping.

Requires backend/db/schema.sql to have already been applied once (this
script only reads/writes `public.data_quality_flags` and
`public.sustainability_targets` from it -- see mis_schema.sql's module
comment on why those two stay outside `mis`).

Run:
    DATABASE_URL=postgresql://moveinsync:moveinsync@localhost:5432/moveinsync \
    DATA_DIR=/absolute/path/to/data \
        python backend/db/real_data/ingest.py

See backend/db/real_data/README.md for the full local workflow, including
spinning up Postgres and applying schema.sql first.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import psycopg

HERE = Path(__file__).resolve().parent
REPO_ROOT_GUESS = HERE.parents[2]  # backend/db/real_data -> backend/db -> backend -> repo root

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://moveinsync:moveinsync@localhost:5432/moveinsync")
DATA_DIR = Path(os.environ.get("DATA_DIR", REPO_ROOT_GUESS / "data"))

RIDE_TRIP_FILES = [
    ("Ride_data _trip-may_2026.csv", "may"),
    ("Ride_data _trip-June_2026.csv", "june"),
    ("Ride_data _trip-July_2026.csv", "july"),
]
RIDE_TRIP_COLUMNS = [
    "business_unit", "office", "product_type", "trip_date", "shift_type", "trip_id",
    "trip_direction", "actual_escort", "vendor_id", "planned_cab_registration",
    "actual_cab_registration", "actual_cab_capacity", "planned_km", "traveled_km",
    "planned_start_epoch", "planned_end_epoch", "actual_start_epoch", "actual_end_epoch",
    "delay_reason", "delay_minutes", "route_source", "actual_cab_fuel_type",
    "is_driver_nc", "is_cab_nc", "trip_nodal", "plannedemployee_cnt",
    "actualemployee_cnt", "noshow_cnt",
]
EMP_DATA_COLUMNS = [
    "business_unit", "office", "product_type", "trip_date", "shift_type", "trip_id",
    "planned_pickup_epoch", "planned_drop_epoch", "actual_pickup_epoch", "actual_drop_epoch",
    "planned_km", "traveled_km", "stwid", "signintype", "gender", "emp_role",
    "boarding_status", "not_boarding_reason", "is_no_show",
]
BILL_DATA_COLUMNS = [
    "business_unit", "office", "vendor", "cycle_start", "cycle_end", "trip_id",
    "contract", "slab_name", "total_trip_km", "trip_cost",
]
ALERTS_DATA_COLUMNS = [
    "business_unit", "trip_id", "stwid", "event_id", "event_type", "start_time",
    "acknowledge_time", "state_text", "severity", "source",
]
TRIP_FEEDBACK_COLUMNS = [
    "business_unit", "trip_id", "trip_type", "trip_date", "stwid", "route_rating",
    "driver_rating", "cab_rating", "safety_rating", "marshal_rating", "creation_time",
]

CHUNK_BYTES = 4 * 1024 * 1024


def log(msg: str) -> None:
    print(f"[ingest] {msg}", flush=True)


def copy_csv(cur: psycopg.Cursor, table: str, columns: list[str], csv_path: Path) -> None:
    if not csv_path.exists():
        raise FileNotFoundError(f"expected CSV at {csv_path} (set DATA_DIR to override)")
    col_list = ", ".join(columns)
    start = time.time()
    n_bytes = 0
    with cur.copy(f"COPY {table} ({col_list}) FROM STDIN WITH (FORMAT csv, HEADER true)") as copy:
        with csv_path.open("rb") as f:
            while True:
                chunk = f.read(CHUNK_BYTES)
                if not chunk:
                    break
                n_bytes += len(chunk)
                copy.write(chunk)
    elapsed = time.time() - start
    log(f"  loaded {csv_path.name} into {table}  ({n_bytes / 1e6:.1f} MB in {elapsed:.1f}s)")


def run_sql_file(cur: psycopg.Cursor, path: Path, split_on_steps: bool = False) -> None:
    sql_text = path.read_text(encoding="utf-8")
    if not split_on_steps:
        cur.execute(sql_text)
        return

    # Split on "-- STEP: <name>" markers so each step can be timed/logged
    # individually. Each block still goes to cur.execute() as ONE call even
    # when it contains several ;-separated statements (including $$-quoted
    # plpgsql function bodies) -- psycopg3 sends a parameter-less execute()
    # via the simple query protocol, which Postgres happily runs as a
    # multi-statement script, dollar-quoting included.
    steps: list[tuple[str, str]] = []
    current_name = "(preamble)"
    current_lines: list[str] = []
    for line in sql_text.splitlines():
        if line.startswith("-- STEP:"):
            if current_lines:
                steps.append((current_name, "\n".join(current_lines)))
            current_name = line[len("-- STEP:"):].strip()
            current_lines = []
        else:
            current_lines.append(line)
    if current_lines:
        steps.append((current_name, "\n".join(current_lines)))

    for name, block in steps:
        if not block.strip():
            continue
        start = time.time()
        cur.execute(block)
        log(f"  step '{name}' done in {time.time() - start:.1f}s")


def table_counts(cur: psycopg.Cursor, tables: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for t in tables:
        cur.execute(f"SELECT count(*) FROM {t}")
        counts[t] = cur.fetchone()[0]
    return counts


def main() -> None:
    log(f"connecting to {DATABASE_URL}")
    log(f"reading CSVs from {DATA_DIR}")

    with psycopg.connect(DATABASE_URL, autocommit=False) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT to_regclass('public.data_quality_flags'), to_regclass('public.sustainability_targets')"
            )
            dqf, targets = cur.fetchone()
            if dqf is None or targets is None:
                sys.exit(
                    "backend/db/schema.sql has not been applied to this database yet "
                    "(public.data_quality_flags / public.sustainability_targets missing). "
                    "Apply it first -- see backend/db/real_data/README.md."
                )

            log("STAGE 1/4: creating stg schema")
            run_sql_file(cur, HERE / "stg_schema.sql")
            conn.commit()

            log("STAGE 2/4: bulk-loading CSVs into stg via COPY")
            for filename, month in RIDE_TRIP_FILES:
                copy_csv(cur, "stg.ride_trip", RIDE_TRIP_COLUMNS, DATA_DIR / filename)
                cur.execute("UPDATE stg.ride_trip SET source_month = %s WHERE source_month IS NULL", (month,))
            copy_csv(cur, "stg.emp_data", EMP_DATA_COLUMNS, DATA_DIR / "emp_Data.csv")
            copy_csv(cur, "stg.bill_data", BILL_DATA_COLUMNS, DATA_DIR / "bill_data.csv")
            copy_csv(cur, "stg.alerts_data", ALERTS_DATA_COLUMNS, DATA_DIR / "alerts_data.csv")
            copy_csv(cur, "stg.trip_feedback", TRIP_FEEDBACK_COLUMNS, DATA_DIR / "trip_feedback.csv")
            conn.commit()
            log("  staging counts: " + str(table_counts(cur, [
                "stg.ride_trip", "stg.emp_data", "stg.bill_data", "stg.alerts_data", "stg.trip_feedback",
            ])))

            log("STAGE 3/4: creating mis schema")
            run_sql_file(cur, HERE / "mis_schema.sql")
            conn.commit()

            log("STAGE 4/4: transforming stg.* -> mis.* (this is the slow part)")
            run_sql_file(cur, HERE / "transform.sql", split_on_steps=True)
            conn.commit()

            log("done. final counts:")
            mis_tables = [
                "mis.vendor", "mis.route", "mis.driver", "mis.team", "mis.employee",
                "mis.trip", "mis.commute", "mis.attendance", "mis.incident",
                "mis.cost", "mis.emission", "mis.feedback",
            ]
            for table, count in table_counts(cur, mis_tables).items():
                log(f"  {table:20s} {count:>10,}")

            cur.execute(
                "SELECT source_table, issue_type, count(*) FROM data_quality_flags "
                "GROUP BY source_table, issue_type ORDER BY source_table, issue_type"
            )
            log("  data_quality_flags by source_table/issue_type:")
            for source_table, issue_type, count in cur.fetchall():
                log(f"    {source_table:16s} {issue_type:20s} {count:>8,}")

    log("ingestion complete.")


if __name__ == "__main__":
    main()
