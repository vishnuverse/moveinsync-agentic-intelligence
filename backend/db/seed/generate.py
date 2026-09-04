"""Synthetic seed data generator for the MoveInSync Agentic Intelligence demo schema.

Populates every table in backend/db/schema.sql with plausible-but-noisy data --
some nulls, a few duplicate-looking rows, a couple of implausible timestamps,
all logged into data_quality_flags rather than silently dropped -- plus a
small number of deliberate, unmistakable anomalies for the live demo:

  - ANOMALY_DELAY_ROUTE_CODE:  sustained sharp delay spike over the last ~25 days
  - ANOMALY_VENDOR_NAME:       clear cost/SLA divergence vs. every other vendor
  - ANOMALY_EMISSIONS_ROUTE_CODE: emissions trending well above the ICE baseline

Run (see backend/db/README.md for the full local workflow):
    DATABASE_URL=postgresql://moveinsync:moveinsync@localhost:5432/moveinsync \
        python backend/db/seed/generate.py
"""

from __future__ import annotations

import os
import random
from datetime import date, datetime, timedelta, timezone

import psycopg

ORG_ID = "moveinsync-demo"
RNG_SEED = 42

NUM_TEAMS = 6
NUM_VENDORS = 5
NUM_ROUTES = 15
DRIVERS_PER_VENDOR = 3
TRIP_WINDOW_DAYS = 60
ATTENDANCE_WINDOW_DAYS = 30

UTC = timezone.utc

ANOMALY_DELAY_ROUTE_INDEX = 0
ANOMALY_VENDOR_INDEX = NUM_VENDORS - 1
ANOMALY_EMISSIONS_ROUTE_INDEX = 7

REGIONS = ["North", "South", "East", "West", "Central"]

FIRST_NAMES = [
    "Aarav", "Vivaan", "Aditya", "Vihaan", "Arjun", "Sai", "Reyansh", "Krishna",
    "Ishaan", "Rohan", "Ananya", "Diya", "Priya", "Isha", "Aadhya", "Kavya",
    "Meera", "Riya", "Sneha", "Neha", "Karthik", "Suresh", "Ramesh", "Anil",
    "Vikram", "Deepa", "Pooja", "Shreya", "Nikhil", "Manish", "Farhan", "Zoya",
    "Aisha", "Imran", "Kabir", "Advait",
]
LAST_NAMES = [
    "Sharma", "Verma", "Iyer", "Nair", "Reddy", "Gupta", "Rao", "Menon",
    "Kulkarni", "Patel", "Singh", "Das", "Bose", "Pillai", "Chatterjee",
    "Mukherjee", "Joshi", "Desai", "Khan", "Shetty",
]

VENDOR_NAMES = [
    "Metro Shuttle Services", "GreenLine Mobility", "Swift Transit Co",
    "UrbanHop Logistics", "CityLink Fleet Solutions",
]

ROUTE_LOCALITIES = [
    "Whitefield", "Electronic City", "HSR Layout", "Koramangala", "Indiranagar",
    "Marathahalli", "BTM Layout", "Hebbal", "Yelahanka", "JP Nagar",
    "Sarjapur Road", "Bellandur", "MG Road", "Rajajinagar", "Banashankari",
]
CAMPUS_NAME = "MoveInSync Corporate Campus"

INCIDENT_TYPES = ["accident", "breakdown", "harassment", "speeding", "route_deviation", "other"]
INCIDENT_SEVERITY_WEIGHTS = [("low", 5), ("medium", 3), ("high", 1), ("critical", 1)]
COST_CATEGORY_WEIGHTS = [("fuel", 5), ("toll", 2), ("driver", 2), ("maintenance", 1), ("other", 1)]


def weighted_choice(pairs: list[tuple[str, int]]) -> str:
    choices, weights = zip(*pairs)
    return random.choices(choices, weights=weights, k=1)[0]


def random_name() -> str:
    return f"{random.choice(FIRST_NAMES)} {random.choice(LAST_NAMES)}"


def as_utc(d: date, hour: int, minute: int) -> datetime:
    return datetime(d.year, d.month, d.day, hour, minute, tzinfo=UTC)


def clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


class Seeder:
    def __init__(self, conn: psycopg.Connection):
        self.conn = conn
        self.cur = conn.cursor()
        self.today = date.today()
        self.counts: dict[str, int] = {}
        self.dq_flag_count = 0

        self.teams: list[dict] = []
        self.employees: list[dict] = []
        self.vendors: list[dict] = []
        self.routes: list[dict] = []
        self.drivers: list[dict] = []
        self.trips_by_route_date: dict[tuple[int, date], dict] = {}
        self.all_trip_ids: list[int] = []

    def run(self) -> None:
        self.reset_tables()
        self.seed_teams_and_employees()
        self.seed_vendors()
        self.seed_routes()
        self.seed_drivers()
        self.seed_route_trips()
        self.seed_route_costs()
        self.seed_safety_incidents()
        self.seed_emissions_log()
        self.seed_sustainability_targets()
        self.seed_commute_and_attendance()
        self.seed_sql_agent_examples()
        self.conn.commit()

    def track(self, table: str, n: int = 1) -> None:
        self.counts[table] = self.counts.get(table, 0) + n

    def flag(self, source_table: str, source_pk, issue_type: str, detail: str, severity: str = "low") -> None:
        self.cur.execute(
            """
            INSERT INTO data_quality_flags (org_id, source_table, source_pk, issue_type, issue_detail, severity)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (ORG_ID, source_table, str(source_pk), issue_type, detail, severity),
        )
        self.dq_flag_count += 1

    def reset_tables(self) -> None:
        # BUGFIX (found live: a routine `docker compose up -d --build backend`
        # -- rebuilding the backend image, which shares a Dockerfile/context
        # with `seed`, invalidates seed's build cache and makes Compose
        # recreate+rerun it -- silently wiped out ~30 minutes of real
        # autonomous-pipeline work and real Sarvam spend). `agent_reports`/
        # `agent_notifications` are RUNTIME OUTPUT the agent system writes
        # (act/db.py's upsert_notification/upsert_report), not seed/input
        # data -- they must never be in a "reset the seed data" TRUNCATE
        # list, synthetic or real-data mode alike. `sql_agent_examples` and
        # `data_quality_flags` stay here: both are genuinely seed-owned
        # (curated examples, and this generator's own detected-issue log).
        tables = [
            "sql_agent_examples", "data_quality_flags",
            "attendance_records", "commute_logs", "sustainability_targets", "emissions_log",
            "vendor_invoices", "route_costs", "safety_incidents", "route_trips",
            "drivers", "routes", "vendors", "employees", "teams",
        ]
        self.cur.execute(f"TRUNCATE {', '.join(tables)} RESTART IDENTITY CASCADE")

    # ------------------------------------------------------------------
    # Master data
    # ------------------------------------------------------------------

    def seed_teams_and_employees(self) -> None:
        team_names = ["Engineering", "Sales", "Customer Success", "Operations", "Finance", "People & Talent"]
        for i in range(NUM_TEAMS):
            region = REGIONS[i % len(REGIONS)]
            self.cur.execute(
                "INSERT INTO teams (org_id, name, region) VALUES (%s, %s, %s) RETURNING id",
                (ORG_ID, team_names[i], region),
            )
            team_id = self.cur.fetchone()[0]
            self.teams.append({"id": team_id, "name": team_names[i], "region": region})
            self.track("teams")

        for team in self.teams:
            team_size = random.randint(5, 8)
            team_employees = []
            for i in range(team_size):
                name = random_name()
                email = None if random.random() < 0.04 else f"{name.lower().replace(' ', '.')}{team['id']}{i}@moveinsync-demo.example"
                if email is None:
                    self.flag("employees", f"pending-{team['id']}-{i}", "null_required_field", "employee row missing email at ingest", "low")
                emp_code = f"EMP-{team['id']:02d}{i:02d}"
                self.cur.execute(
                    """
                    INSERT INTO employees (org_id, employee_code, full_name, email, team_id, home_location, work_location, status)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, 'active') RETURNING id
                    """,
                    (ORG_ID, emp_code, name, email, team["id"], random.choice(ROUTE_LOCALITIES), CAMPUS_NAME),
                )
                emp_id = self.cur.fetchone()[0]
                emp = {"id": emp_id, "team_id": team["id"], "region": team["region"]}
                team_employees.append(emp)
                self.employees.append(emp)
                self.track("employees")

            manager = random.choice(team_employees)
            self.cur.execute("UPDATE teams SET line_manager_id = %s WHERE id = %s", (manager["id"], team["id"]))
            self.cur.execute("UPDATE employees SET line_manager_id = %s WHERE team_id = %s AND id != %s", (manager["id"], team["id"], manager["id"]))
            team["employees"] = team_employees
            team["manager_id"] = manager["id"]

    def seed_vendors(self) -> None:
        for i in range(NUM_VENDORS):
            is_bad = i == ANOMALY_VENDOR_INDEX
            cost_per_km = round(random.uniform(27.0, 34.0), 2) if is_bad else round(random.uniform(11.5, 18.5), 2)
            sla_target = round(random.uniform(90.0, 93.0), 2) if is_bad else round(random.uniform(94.0, 97.0), 2)
            start = self.today - timedelta(days=random.randint(400, 1200))
            self.cur.execute(
                """
                INSERT INTO vendors (org_id, name, contract_start_date, contract_end_date, sla_target_pct, cost_per_km_inr, region, status)
                VALUES (%s, %s, %s, %s, %s, %s, %s, 'active') RETURNING id
                """,
                (ORG_ID, VENDOR_NAMES[i], start, start + timedelta(days=730), sla_target, cost_per_km, random.choice(REGIONS)),
            )
            vendor_id = self.cur.fetchone()[0]
            self.vendors.append({"id": vendor_id, "name": VENDOR_NAMES[i], "cost_per_km_inr": cost_per_km, "is_bad": is_bad})
            self.track("vendors")

    def seed_routes(self) -> None:
        good_vendors = [v for v in self.vendors if not v["is_bad"]]
        bad_vendor = next(v for v in self.vendors if v["is_bad"])
        for i in range(NUM_ROUTES):
            vendor = bad_vendor if i in self._bad_vendor_route_indices() else random.choice(good_vendors)
            locality = ROUTE_LOCALITIES[i % len(ROUTE_LOCALITIES)]
            region_category = REGIONS[i % len(REGIONS)]
            distance_km = round(random.uniform(8.0, 34.0), 2)
            shift_type = "morning" if i % 2 == 0 else "evening"
            dep_hour, dep_minute = (7, random.choice([0, 15, 30, 45])) if shift_type == "morning" else (18, random.choice([0, 15, 30, 45]))
            vehicle_type = "EV" if i == 3 else ("hybrid" if i in (5, 9) else "ICE")
            self.cur.execute(
                """
                INSERT INTO routes (org_id, route_code, name, origin, destination, region, vendor_id, scheduled_distance_km, shift_type, status)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'active') RETURNING id
                """,
                (
                    ORG_ID, f"RT-{i + 1:03d}", f"{locality} - Campus Shuttle", locality, CAMPUS_NAME,
                    region_category, vendor["id"], distance_km, shift_type,
                ),
            )
            route_id = self.cur.fetchone()[0]
            self.routes.append({
                "id": route_id, "index": i, "vendor_id": vendor["id"], "vendor_is_bad": vendor["is_bad"],
                "distance_km": distance_km, "dep_hour": dep_hour, "dep_minute": dep_minute,
                "vehicle_type": vehicle_type, "region": region_category, "locality": locality,
            })
            self.track("routes")

    def _bad_vendor_route_indices(self) -> set[int]:
        # Deliberately avoid ANOMALY_DELAY_ROUTE_INDEX and ANOMALY_EMISSIONS_ROUTE_INDEX
        # so the three demo anomalies stay independently discoverable, not overlapping.
        return {4, 11, 13}

    def seed_drivers(self) -> None:
        for vendor in self.vendors:
            vendor["driver_ids"] = []
            for i in range(DRIVERS_PER_VENDOR):
                license_number = None if random.random() < 0.03 else f"DL-{vendor['id']:02d}{i:02d}-{random.randint(1000, 9999)}"
                if license_number is None:
                    self.flag("drivers", f"pending-{vendor['id']}-{i}", "null_required_field", "driver row missing license_number at ingest", "medium")
                self.cur.execute(
                    """
                    INSERT INTO drivers (org_id, vendor_id, driver_code, full_name, license_number, phone, status)
                    VALUES (%s, %s, %s, %s, %s, %s, 'active') RETURNING id
                    """,
                    (ORG_ID, vendor["id"], f"DRV-{vendor['id']:02d}{i:02d}", random_name(), license_number, f"9{random.randint(100000000, 999999999)}"),
                )
                driver_id = self.cur.fetchone()[0]
                self.drivers.append({"id": driver_id, "vendor_id": vendor["id"]})
                vendor["driver_ids"].append(driver_id)
                self.track("drivers")

    # ------------------------------------------------------------------
    # route_trips (+ deliberately messy rows)
    # ------------------------------------------------------------------

    def seed_route_trips(self) -> None:
        duplicate_budget = 6
        malformed_budget = 4
        missing_actual_budget = 10
        out_of_range_budget = 5

        for day_offset in range(TRIP_WINDOW_DAYS, 0, -1):
            trip_date = self.today - timedelta(days=day_offset)
            is_weekday = trip_date.weekday() < 5
            for route in self.routes:
                operates = random.random() < (0.92 if is_weekday else 0.15)
                if not operates:
                    continue

                scheduled_departure = as_utc(trip_date, route["dep_hour"], route["dep_minute"])
                duration_minutes = route["distance_km"] / 22.0 * 60 + 6
                scheduled_arrival = scheduled_departure + timedelta(minutes=duration_minutes)

                is_cancelled = random.random() < 0.03
                delay_minutes = self._delay_minutes_for(route, day_offset)
                actual_departure = scheduled_departure + timedelta(minutes=random.uniform(-2, 5))
                actual_arrival = scheduled_arrival + timedelta(minutes=delay_minutes)
                passenger_count = random.randint(12, 42)

                status = "cancelled" if is_cancelled else "completed"
                if is_cancelled:
                    actual_departure = None
                    actual_arrival = None
                    passenger_count = 0

                if not is_cancelled and out_of_range_budget > 0 and random.random() < 0.006:
                    passenger_count = random.choice([-3, 250])
                    out_of_range_budget -= 1

                trip_id = self._insert_trip(
                    route, trip_date, scheduled_departure, scheduled_arrival,
                    actual_departure, actual_arrival, passenger_count, status,
                )

                if not is_cancelled and missing_actual_budget > 0 and random.random() < 0.01:
                    self.cur.execute("UPDATE route_trips SET actual_arrival = NULL WHERE id = %s", (trip_id,))
                    self.flag("route_trips", trip_id, "null_required_field", "actual_arrival missing on a trip marked completed", "medium")
                    missing_actual_budget -= 1
                elif not is_cancelled and malformed_budget > 0 and random.random() < 0.008:
                    implausible = datetime(1999, 1, 1, 0, 0, tzinfo=UTC)
                    self.cur.execute("UPDATE route_trips SET actual_arrival = %s WHERE id = %s", (implausible, trip_id))
                    self.flag("route_trips", trip_id, "malformed_timestamp", "actual_arrival outside plausible range for trip_date", "medium")
                    malformed_budget -= 1

                self.trips_by_route_date[(route["id"], trip_date)] = {
                    "id": trip_id, "scheduled_arrival": scheduled_arrival, "actual_arrival": actual_arrival,
                    "delay_minutes": delay_minutes, "status": status, "route": route,
                    "passenger_count": passenger_count,
                }
                if status == "completed":
                    self.all_trip_ids.append(trip_id)

                if duplicate_budget > 0 and status == "completed" and random.random() < 0.006:
                    dup_id = self._insert_trip(
                        route, trip_date, scheduled_departure, scheduled_arrival,
                        actual_departure, actual_arrival, passenger_count + random.randint(-2, 2), status,
                    )
                    self.flag("route_trips", trip_id, "duplicate_row", f"looks like a duplicate ingest of trip {trip_id} (same route/date/schedule)", "low")
                    self.flag("route_trips", dup_id, "duplicate_row", f"looks like a duplicate ingest of trip {trip_id} (same route/date/schedule)", "low")
                    duplicate_budget -= 1

    def _delay_minutes_for(self, route: dict, day_offset: int) -> float:
        if route["index"] == ANOMALY_DELAY_ROUTE_INDEX:
            if day_offset <= 25:
                return random.uniform(28, 58)
            return max(0.0, random.gauss(6, 4))
        if route["vendor_is_bad"]:
            return max(0.0, random.uniform(9, 22))
        return max(0.0, random.gauss(4, 4)) if random.random() > 0.05 else random.uniform(12, 20)

    def _insert_trip(self, route, trip_date, scheduled_departure, scheduled_arrival, actual_departure, actual_arrival, passenger_count, status) -> int:
        driver_id = random.choice(self._vendor_by_id(route["vendor_id"])["driver_ids"])
        self.cur.execute(
            """
            INSERT INTO route_trips (org_id, route_id, driver_id, trip_date, scheduled_departure, scheduled_arrival,
                                      actual_departure, actual_arrival, passenger_count, status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id
            """,
            (
                ORG_ID, route["id"], driver_id, trip_date, scheduled_departure, scheduled_arrival,
                actual_departure, actual_arrival, passenger_count, status,
            ),
        )
        trip_id = self.cur.fetchone()[0]
        self.track("route_trips")
        return trip_id

    def _vendor_by_id(self, vendor_id: int) -> dict:
        return next(v for v in self.vendors if v["id"] == vendor_id)

    # ------------------------------------------------------------------
    # route_costs
    # ------------------------------------------------------------------

    def seed_route_costs(self) -> None:
        rows = []
        for (route_id, trip_date), trip in self.trips_by_route_date.items():
            if trip["status"] != "completed":
                continue
            route = trip["route"]
            vendor = self._vendor_by_id(route["vendor_id"])
            rate = vendor["cost_per_km_inr"] * random.uniform(0.93, 1.09)
            total_cost = round(rate * route["distance_km"], 2)
            rows.append((
                ORG_ID, route_id, route["vendor_id"], trip["id"], trip_date, route["distance_km"],
                trip["passenger_count"], total_cost, round(total_cost / route["distance_km"], 2), weighted_choice(COST_CATEGORY_WEIGHTS),
            ))
        self.cur.executemany(
            """
            INSERT INTO route_costs (org_id, route_id, vendor_id, trip_id, cost_date, distance_km,
                                      passenger_count, total_cost_inr, cost_per_km_inr, cost_category)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            rows,
        )
        self.track("route_costs", len(rows))

    # ------------------------------------------------------------------
    # safety_incidents
    # ------------------------------------------------------------------

    def seed_safety_incidents(self) -> None:
        rows = []
        for (route_id, trip_date), trip in self.trips_by_route_date.items():
            if trip["status"] != "completed":
                continue
            route = trip["route"]
            base_rate = 0.02
            if route["vendor_is_bad"] or route["index"] == ANOMALY_DELAY_ROUTE_INDEX:
                base_rate = 0.07
            if random.random() >= base_rate:
                continue
            occurred_at = trip["scheduled_arrival"] - timedelta(minutes=random.randint(0, 30))
            driver_id = random.choice(self._vendor_by_id(route["vendor_id"])["driver_ids"])
            rows.append((
                ORG_ID, trip["id"], route_id, driver_id, weighted_choice([(t, 1) for t in INCIDENT_TYPES]),
                weighted_choice(INCIDENT_SEVERITY_WEIGHTS), "Auto-generated synthetic incident for demo purposes.",
                occurred_at, occurred_at + timedelta(minutes=random.randint(5, 240)),
                random.choice(["open", "investigating", "resolved", "closed"]),
            ))
        self.cur.executemany(
            """
            INSERT INTO safety_incidents (org_id, trip_id, route_id, driver_id, incident_type, severity, description,
                                           occurred_at, reported_at, status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            rows,
        )
        self.track("safety_incidents", len(rows))

    # ------------------------------------------------------------------
    # emissions_log
    # ------------------------------------------------------------------

    def seed_emissions_log(self) -> None:
        factor_by_type = {"ICE": (82, 8), "hybrid": (48, 6), "EV": (10, 3)}
        rows = []
        for (route_id, trip_date), trip in self.trips_by_route_date.items():
            if trip["status"] != "completed":
                continue
            route = trip["route"]
            day_offset = (self.today - trip_date).days
            if route["index"] == ANOMALY_EMISSIONS_ROUTE_INDEX:
                ramp_progress = clamp(1 - (day_offset / TRIP_WINDOW_DAYS), 0.0, 1.0)
                mean_factor = 88 + ramp_progress * 68
                factor = max(60.0, random.gauss(mean_factor, 7))
            else:
                mean, sd = factor_by_type[route["vehicle_type"]]
                factor = max(3.0, random.gauss(mean, sd))

            passenger_count = trip["passenger_count"]
            co2_grams = round(factor * route["distance_km"] * passenger_count, 2)
            rows.append((
                ORG_ID, route_id, trip["id"], trip_date, route["distance_km"], passenger_count,
                co2_grams, round(factor, 3), route["vehicle_type"],
            ))
        self.cur.executemany(
            """
            INSERT INTO emissions_log (org_id, route_id, trip_id, log_date, distance_km, passenger_count,
                                        co2_grams, co2_per_passenger_km, vehicle_type)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            rows,
        )
        self.track("emissions_log", len(rows))

    # ------------------------------------------------------------------
    # sustainability_targets (plan §8 concrete benchmarks)
    # ------------------------------------------------------------------

    def seed_sustainability_targets(self) -> None:
        rows = [
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
        self.cur.executemany(
            """
            INSERT INTO sustainability_targets (org_id, metric_name, target_value, threshold_value, unit, period, notes)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            rows,
        )
        self.track("sustainability_targets", len(rows))

    # ------------------------------------------------------------------
    # commute_logs / attendance_records
    # ------------------------------------------------------------------

    def seed_commute_and_attendance(self) -> None:
        routes_by_region: dict[str, list[dict]] = {}
        for route in self.routes:
            routes_by_region.setdefault(route["region"], []).append(route)

        for team in self.teams:
            for emp in team["employees"]:
                candidates = routes_by_region.get(emp["region"]) or self.routes
                emp["home_route"] = random.choice(candidates)

        commute_rows = []
        attendance_rows = []

        for day_offset in range(ATTENDANCE_WINDOW_DAYS, 0, -1):
            work_date = self.today - timedelta(days=day_offset)
            if work_date.weekday() >= 5:
                continue

            for emp in self.employees:
                route = emp["home_route"]
                trip = self.trips_by_route_date.get((route["id"], work_date))
                uses_shuttle = trip is not None and trip["status"] == "completed" and random.random() < 0.85

                delay_minutes = 0.0
                if uses_shuttle:
                    boarding = trip["scheduled_arrival"] - timedelta(minutes=random.randint(20, 45))
                    alighting = trip["actual_arrival"] if trip["actual_arrival"] else trip["scheduled_arrival"]
                    delay_minutes = trip["delay_minutes"]
                    commute_rows.append((
                        ORG_ID, emp["id"], trip["id"], route["id"], work_date, boarding, alighting, "shuttle", "completed",
                    ))
                else:
                    mode = random.choice(["cab", "walk_in", "wfh"])
                    commute_rows.append((
                        ORG_ID, emp["id"], None, None, work_date, None, None, mode, "completed",
                    ))

                clock_in_base = as_utc(work_date, 9, 30)
                if uses_shuttle and delay_minutes > 15 and random.random() < 0.75:
                    late_minutes = int(clamp(delay_minutes - random.uniform(0, 10), 5, 90))
                    status = "late"
                elif random.random() < 0.05:
                    late_minutes = random.randint(5, 45)
                    status = "late"
                elif random.random() < 0.02:
                    late_minutes = 0
                    status = "absent"
                elif not uses_shuttle and random.random() < 0.3:
                    late_minutes = 0
                    status = "wfh"
                else:
                    late_minutes = 0
                    status = "present"

                clock_in = None if status == "absent" else clock_in_base + timedelta(minutes=late_minutes + random.uniform(-5, 5))
                clock_out = None if status == "absent" else as_utc(work_date, 18, 30) + timedelta(minutes=random.uniform(-15, 45))
                attendance_rows.append((
                    ORG_ID, emp["id"], work_date, clock_in, clock_out, status, late_minutes,
                ))

        self.cur.executemany(
            """
            INSERT INTO commute_logs (org_id, employee_id, trip_id, route_id, log_date, boarding_time, alighting_time, mode, status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            commute_rows,
        )
        self.track("commute_logs", len(commute_rows))

        self.cur.executemany(
            """
            INSERT INTO attendance_records (org_id, employee_id, work_date, clock_in_time, clock_out_time, status, late_minutes)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            attendance_rows,
        )
        self.track("attendance_records", len(attendance_rows))

    # ------------------------------------------------------------------
    # sql_agent_examples (pgvector)
    # ------------------------------------------------------------------

    def seed_sql_agent_examples(self) -> None:
        examples = [
            (
                "Which routes breached the 92% SLA threshold in the last 7 days?",
                "SELECT r.route_code, r.name, COUNT(*) FILTER (WHERE t.actual_arrival > t.scheduled_arrival + INTERVAL '15 minutes') * 100.0 / COUNT(*) AS breach_pct "
                "FROM route_trips t JOIN routes r ON r.id = t.route_id "
                "WHERE t.trip_date >= CURRENT_DATE - INTERVAL '7 days' AND t.status = 'completed' "
                "GROUP BY r.route_code, r.name HAVING COUNT(*) FILTER (WHERE t.actual_arrival > t.scheduled_arrival + INTERVAL '15 minutes') * 100.0 / COUNT(*) > 8 "
                "ORDER BY breach_pct DESC;",
                "route_trips(scheduled_arrival, actual_arrival, status), routes(route_code, name)",
            ),
            (
                "What is the average cost per passenger-km by vendor this month?",
                "SELECT v.name, SUM(c.total_cost_inr) / NULLIF(SUM(c.distance_km * c.passenger_count), 0) AS cost_per_passenger_km "
                "FROM route_costs c JOIN vendors v ON v.id = c.vendor_id "
                "WHERE c.cost_date >= date_trunc('month', CURRENT_DATE) GROUP BY v.name ORDER BY cost_per_passenger_km DESC;",
                "route_costs(vendor_id, total_cost_inr, distance_km, passenger_count, cost_date), vendors(name)",
            ),
            (
                "Show me safety incidents by severity for the last 30 days.",
                "SELECT severity, COUNT(*) FROM safety_incidents WHERE occurred_at >= CURRENT_DATE - INTERVAL '30 days' GROUP BY severity ORDER BY COUNT(*) DESC;",
                "safety_incidents(severity, occurred_at)",
            ),
            (
                "Which team has the highest correlation between late attendance and shuttle delays?",
                "SELECT t.name, COUNT(*) FILTER (WHERE a.status = 'late') * 100.0 / COUNT(*) AS late_pct "
                "FROM attendance_records a JOIN employees e ON e.id = a.employee_id JOIN teams t ON t.id = e.team_id "
                "GROUP BY t.name ORDER BY late_pct DESC;",
                "attendance_records(employee_id, status), employees(team_id), teams(name)",
            ),
            (
                "What is our emissions trend for the last quarter compared to the ICE baseline?",
                "SELECT date_trunc('week', log_date) AS week, AVG(co2_per_passenger_km) FROM emissions_log "
                "WHERE log_date >= CURRENT_DATE - INTERVAL '90 days' GROUP BY week ORDER BY week;",
                "emissions_log(log_date, co2_per_passenger_km), sustainability_targets(metric_name, target_value)",
            ),
            (
                "List vendors whose SLA target is below our 92% breach threshold.",
                "SELECT name, sla_target_pct FROM vendors WHERE sla_target_pct < 92 ORDER BY sla_target_pct ASC;",
                "vendors(name, sla_target_pct), sustainability_targets(metric_name, threshold_value)",
            ),
        ]
        rows = []
        for question, sql, table_context in examples:
            embedding = "[" + ",".join(f"{random.uniform(-1, 1):.5f}" for _ in range(1536)) + "]"
            rows.append((ORG_ID, question, sql, table_context, embedding))

        self.cur.executemany(
            """
            INSERT INTO sql_agent_examples (org_id, question, sql, table_context, embedding)
            VALUES (%s, %s, %s, %s, %s::vector)
            """,
            rows,
        )
        self.track("sql_agent_examples", len(rows))

    # ------------------------------------------------------------------

    def print_summary(self) -> None:
        print("\nSeed complete. Row counts:")
        for table in sorted(self.counts):
            print(f"  {table:<24} {self.counts[table]}")
        print(f"  {'data_quality_flags':<24} {self.dq_flag_count}")
        anomaly_route = self.routes[ANOMALY_DELAY_ROUTE_INDEX]
        anomaly_vendor = self.vendors[ANOMALY_VENDOR_INDEX]
        emissions_route = self.routes[ANOMALY_EMISSIONS_ROUTE_INDEX]
        print("\nSeeded anomalies for the demo:")
        print(f"  delay spike route:      RT-{anomaly_route['index'] + 1:03d} (route id {anomaly_route['id']})")
        print(f"  cost/SLA divergence:     {anomaly_vendor['name']} (vendor id {anomaly_vendor['id']})")
        print(f"  over-target emissions:   RT-{emissions_route['index'] + 1:03d} (route id {emissions_route['id']})")


def main() -> None:
    random.seed(RNG_SEED)
    database_url = os.environ.get("DATABASE_URL", "postgresql://moveinsync:moveinsync@localhost:5432/moveinsync")
    with psycopg.connect(database_url, autocommit=False) as conn:
        seeder = Seeder(conn)
        seeder.run()
        seeder.print_summary()


if __name__ == "__main__":
    main()
