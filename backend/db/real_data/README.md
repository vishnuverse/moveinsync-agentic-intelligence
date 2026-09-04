# Real MoveInSync data ingestion

Loads the real dataset in `data/*.csv` (repo root, gitignored -- see
`data/Dictionary/README.md` for what's in it) into Postgres, cleans it, and
builds the `mis` schema that `backend/config/data_contract.yaml` now points
at by default. The synthetic `public` schema (`backend/db/schema.sql` +
`backend/db/seed/generate.py`) is untouched and still works as a fallback --
see "Switching back to synthetic data" below.

## Pipeline

```
data/*.csv  --COPY-->  stg.*  (raw text, one table per source file)
                          |
                          v  transform.sql (cleaning, typing, dimension
                          |  derivation, data-quality flagging)
                          v
                        mis.*  (typed, cleaned, contract-mapped tables)
```

- `stg_schema.sql` -- staging tables, TEXT columns in the exact physical
  column order of each source CSV (COPY maps positionally).
- `mis_schema.sql` -- the real canonical schema. Every table has an inline
  comment explaining a real judgment call (vendor/route/driver dimension
  grain, the synthetic team/line-manager hierarchy, the cited emission
  factors, the severity remapping) -- read it before trusting a number out
  of this schema.
- `transform.sql` -- the actual cleaning/derivation SQL, run in dependency
  order (vendor/route/driver dimensions -> trip -> commute/attendance ->
  incident/cost/emission/feedback), split into `-- STEP:` blocks that
  `ingest.py` times and logs individually.
- `ingest.py` -- orchestrates all of the above: bulk-loads via `COPY` (not
  row-by-row inserts -- this is millions of rows), then runs the transform,
  then prints row counts per table and a `data_quality_flags` breakdown.

## Run it

1. Start Postgres (pgvector image, matches the rest of the stack):

   ```bash
   docker run -d --name mis-postgres \
     -e POSTGRES_USER=moveinsync -e POSTGRES_PASSWORD=moveinsync -e POSTGRES_DB=moveinsync \
     -p 5432:5432 pgvector/pgvector:pg16
   ```

2. Apply the synthetic reference schema first -- `mis_schema.sql` deliberately
   does NOT create `data_quality_flags` / `sustainability_targets` (both are
   shared infra tables read/written by both the synthetic and real-data
   paths; see `mis_schema.sql`'s module comment), so it must exist before
   `ingest.py` runs:

   ```bash
   psql "$DATABASE_URL" -f backend/db/schema.sql
   ```

3. Point `DATA_DIR` at wherever `data/` actually lives on disk (repo root by
   default) and run the ingestion:

   ```bash
   DATABASE_URL=postgresql://moveinsync:moveinsync@localhost:5432/moveinsync \
   DATA_DIR=/absolute/path/to/moveinsync-agentic-intelligence/data \
       python backend/db/real_data/ingest.py
   ```

   Re-running is safe any time the CSVs change: both `stg` and `mis` are
   `DROP SCHEMA ... CASCADE`'d and rebuilt from scratch on every run.

4. The default `backend/config/data_contract.yaml` already points at `mis.*`
   -- no further config needed for the backend to read real data.

## Switching back to synthetic data

`backend/app/contracts/loader.py` already supports a `DATA_CONTRACT_PATH` env
var (this predates this task -- no loader code changed):

```bash
DATA_CONTRACT_PATH=backend/config/data_contract.synthetic.yaml <run backend/seed>
```

`backend/config/data_contract.synthetic.yaml` is a verbatim copy of the
contract that shipped before real data landed, still pointing at
`backend/db/schema.sql` + `backend/db/seed/generate.py`'s `public.*` tables.
Both schemas (`public` and `mis`) can coexist in the same database at once;
which one the app reads is purely which contract file is active.

## What's real vs. synthesized

Read `mis_schema.sql`'s per-table comments for the full reasoning. Short
version:

| Entity | Real? |
|---|---|
| trip, cost, incident, commute, feedback | Real, cleaned/typed from the CSVs |
| vendor, route, driver | Real underlying data, but the *dimension grain* is derived/approximated (no master tables exist in the raw export) -- driver in particular is a `(vendor, cab plate)` proxy, not a real driver identity |
| emission | Fully derived: real `traveled_km` x a cited published emission factor per fuel type -- not in the raw data at all |
| attendance | Repurposed as commute-derived (LOGIN-leg pickup delay), not real HR clock-in/out data -- correlated with commute delay *by construction* |
| **team, line-manager hierarchy** | **Fully synthetic.** No org hierarchy exists in the raw data. Employees are grouped into ~20-person teams by (business_unit, most-common office) with one fabricated manager per team. This is enrichment layered on top of real rider data, not sourced from MoveInSync -- do not present it as real to a judge or end user. |

## Data-quality flags

Two independent sources both write into the same `public.data_quality_flags`
table:

1. **Ingest-time flags** (`transform.sql`): unparseable `trip_date`/`trip_id`,
   negative `planned_km`/`traveled_km` (clipped to NULL), the stray `"False"`
   literal in `alerts_data.severity` (cleaned to NULL), `bill_data` rows with
   `total_trip_km <= 0.1` (cost-per-km left NULL rather than computing `inf`
   or a multi-million-INR/km outlier), orphaned cross-file `trip_id`
   references (a `bill_data`/`alerts_data`/`emp_data`/`trip_feedback` row
   whose `trip_id` has no matching `ride_data_trip` row), and **an
   undocumented quirk found during ingestion, not one of the Dictionary's
   9**: `trip_id` is not quite globally unique across the three monthly
   files -- ~6,754 values (~1.1% of trips) collide across two different
   `(business_unit, month)` pairs, every one of them crossing a
   business_unit boundary. `mis.trip` can only keep one row per id (kept:
   the later-completing trip by `actual_end_epoch`); see `mis_schema.sql`'s
   `mis.trip` comment for the disclosed consequence for the handful of
   cross-file rows referencing the trip that got dropped. A second
   undocumented quirk: 189 `bill_data` rows carry a **negative** `trip_cost`
   (some under a literal `trip_id="OverHead"` -- a billing correction line,
   not a per-trip fare); left in, these corrupted vendor cost-per-km
   averages by orders of magnitude (a -448,973 INR/km "rate" was observed on
   a real vendor before this guard was added), so they're flagged and
   excluded from `mis.cost` entirely rather than clipped.
2. **Live sense-layer flags** (`backend/app/graph/sense/nodes.py`'s
   `flag_data_quality`, unmodified): null `actual_time` on completed trips,
   malformed timestamps, duplicate trips, out-of-range `passenger_count`,
   null employee email (essentially never fires against `mis.employee` --
   see the synthetic-placeholder-email note below), and null driver
   `license_number` (fires for real, since no real license data exists).

**Why `employee.email` is synthesized instead of left NULL+flagged:** every
real `emp_data` row is missing email (it's simply not in the anonymised
export), so leaving it NULL would make `flag_data_quality` log one
near-identical "missing email" flag per employee (~25K rows) for a gap that
isn't really an actionable data-quality issue -- it's a structural property
of the anonymisation, not a real anomaly in an otherwise-real field. A
deterministic placeholder (`stw<id>@<org>.mis-demo.internal`) is synthesized
instead and clearly labeled as such in `mis_schema.sql`, keeping
`data_quality_flags` focused on gaps worth a human's attention (e.g. driver
`license_number`, which genuinely has no substitute and is left NULL+flagged
for real).
