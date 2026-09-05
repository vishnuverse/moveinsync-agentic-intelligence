"""Reference-data seeder for the MoveInSync Agentic Intelligence schema.

The project runs solely on the REAL dataset (the `mis` schema, populated by
backend/db/real_data/ingest.py). There is no synthetic business data any more.
The only thing that still needs seeding into `public` is the small set of
reference benchmarks the reason layer reads to judge a metric good/bad:
`sustainability_targets`.

This script is deliberately narrow -- it does NOT touch business data, the
data-quality log, or the act-layer output tables:
  * data_quality_flags  -- owned by the real ingest (transform.sql) + runtime
    sense layer; must not be wiped here.
  * agent_notifications / agent_reports -- runtime output of the agent system
    (act/db.py); never reset here.

Run (see backend/db/README.md):
    DATABASE_URL=postgresql://moveinsync:moveinsync@localhost:5432/moveinsync \
        python backend/db/seed/generate.py
"""

from __future__ import annotations

import os

import psycopg

ORG_ID = "moveinsync-demo"

# plan §8 concrete benchmarks -- the external targets the reason layer compares
# real metrics against (research_agent + emissions detector read these by name).
SUSTAINABILITY_TARGETS = [
    (
        ORG_ID, "cost_efficiency_inr_per_passenger_km", 15.00, 18.00, "INR_per_passenger_km", "ongoing",
        "Industry-reasonable range for corporate shuttle service is INR 12-18 per passenger-km; "
        "target_value is the midpoint, threshold_value is the upper bound above which cost efficiency is flagged.",
    ),
    (
        ORG_ID, "sla_timeliness_pct", 95.00, 92.00, "percent", "ongoing",
        "95% on-time arrival is the target; below 92% is flagged as an actionable SLA breach.",
    ),
    (
        ORG_ID, "carbon_gco2_per_passenger_km", 82.00, 82.00, "gCO2_per_passenger_km", "ongoing",
        "82 gCO2/passenger-km is the standard ICE-fleet baseline used to judge whether an emissions trend is good or bad.",
    ),
]


def seed_reference(conn: psycopg.Connection) -> int:
    """Reset + reseed sustainability_targets only. Returns rows inserted."""
    with conn.cursor() as cur:
        # Only sustainability_targets is reset here -- never data_quality_flags
        # (real ingest owns it) or the agent output tables.
        cur.execute("TRUNCATE sustainability_targets RESTART IDENTITY")
        cur.executemany(
            """
            INSERT INTO sustainability_targets (org_id, metric_name, target_value, threshold_value, unit, period, notes)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            SUSTAINABILITY_TARGETS,
        )
    conn.commit()
    return len(SUSTAINABILITY_TARGETS)


def main() -> None:
    database_url = os.environ.get("DATABASE_URL", "postgresql://moveinsync:moveinsync@localhost:5432/moveinsync")
    with psycopg.connect(database_url, autocommit=False) as conn:
        n = seed_reference(conn)
    print(f"Reference seed complete. sustainability_targets: {n} rows.")


if __name__ == "__main__":
    main()
