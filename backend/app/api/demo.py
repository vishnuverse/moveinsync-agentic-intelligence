"""POST /api/demo/replay -- the "Simulate live day" demo control (SP-A).

Injects real, re-timestamped historical rows for a chosen scenario (via
backend/db/real_data/replay.py) and then runs the autonomy pipeline once
synchronously (app.graph.supervisor.run_pipeline) so those freshly-injected
rows are sensed -> reasoned -> acted on immediately and their act-layer
events are published to Redis -> the `/api/ws/{persona}` WebSocket the Live
feed listens on. Running the pipeline inline (rather than waiting on the
scheduler service's next tick) is what guarantees the demo "lights up" the
moment the button is pressed, even when the scheduler isn't running.

This is the ONLY backend file SP-A owns; it deliberately does not touch the
graph/reason/act/supervisor internals (a concurrent session owns those) --
it only *calls* the already-public run_pipeline() entry point and the
replay tool's already-public fetch_candidate_trip_ids()/replay_one().
"""

from __future__ import annotations

import importlib.util
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import default_org_id

logger = logging.getLogger(__name__)

router = APIRouter(tags=["demo"])

# The replay tool lives at backend/db/real_data/replay.py, which is NOT an
# importable package (`db` has no __init__.py and isn't on the import path).
# Load it by file path relative to this file: api -> app -> backend, then
# db/real_data/replay.py. Loaded lazily so a missing file / import error is
# reported as a graceful 503 at request time, never a hard crash at startup.
_BACKEND_ROOT = Path(__file__).resolve().parents[2]
_REPLAY_PATH = _BACKEND_ROOT / "db" / "real_data" / "replay.py"

_DEFAULT_DSN = "postgresql://moveinsync:moveinsync@localhost:5432/moveinsync"

# Kept in sync with replay.py's _SCENARIO_QUERIES keys -- surfaced so the
# frontend scenario picker and error messages don't hardcode a stale list.
SCENARIOS = ["delay_spike", "escort_violation", "billing_discrepancy", "emissions_over_target"]


def _load_replay():
    """Import replay.py by path. Raises on failure; caller maps to 503."""
    spec = importlib.util.spec_from_file_location("misync_replay", _REPLAY_PATH)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load replay module at {_REPLAY_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _dsn() -> str:
    return os.environ.get("DATABASE_URL", _DEFAULT_DSN)


class ReplayRequest(BaseModel):
    scenario: str = Field(..., description="one of demo.SCENARIOS")
    count: int = Field(default=3, ge=1, le=25)
    org_id: str | None = None


class ReplayResponse(BaseModel):
    scenario: str
    org_id: str
    injected_trip_ids: list[int]
    new_trip_ids: list[int]
    pipeline_summary: list[dict[str, Any]]


@router.get("/demo/scenarios")
def list_scenarios() -> dict[str, list[str]]:
    return {"scenarios": SCENARIOS}


@router.post("/demo/replay", response_model=ReplayResponse)
def replay(body: ReplayRequest) -> ReplayResponse:
    if body.scenario not in SCENARIOS:
        raise HTTPException(
            status_code=422,
            detail=f"unknown scenario {body.scenario!r}; expected one of {SCENARIOS}",
        )

    org_id = body.org_id or default_org_id()

    # --- 1. Import the replay tool + open a DB connection (graceful 503) ---
    try:
        replay_mod = _load_replay()
    except Exception as exc:  # noqa: BLE001 - report as service-unavailable
        logger.exception("demo.replay: could not load replay tool")
        raise HTTPException(status_code=503, detail=f"replay tool unavailable: {exc}") from exc

    try:
        import psycopg  # local import: psycopg is a replay dependency, not an app-wide one
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=f"psycopg unavailable: {exc}") from exc

    injected: list[int] = []
    new_trip_ids: list[int] = []
    try:
        conn = psycopg.connect(_dsn())
    except Exception as exc:  # noqa: BLE001 - DB down / bad DSN
        logger.exception("demo.replay: DB connection failed")
        raise HTTPException(status_code=503, detail=f"database unavailable: {exc}") from exc

    # --- 2. Select real candidate rows and re-inject them at "now" ---
    try:
        candidates = replay_mod.fetch_candidate_trip_ids(conn, body.scenario, org_id, body.count)
        if not candidates:
            raise HTTPException(
                status_code=404,
                detail=(
                    f"no real rows found for scenario={body.scenario!r} org_id={org_id!r} -- "
                    "ingest real data first (backend/db/real_data/ingest.py)"
                ),
            )
        # escort_violation is the one scenario whose detector is hour-of-day
        # sensitive (late-night 21:00-06:00 window) -- preserve the original
        # clock time when re-timestamping so the injected trip stays late-night.
        preserve_tod = body.scenario == "escort_violation"
        for trip_id in candidates:
            now = datetime.now(timezone.utc)
            result = replay_mod.replay_one(conn, trip_id, now, preserve_time_of_day=preserve_tod)
            injected.append(result["source_trip_id"])
            new_trip_ids.append(result["new_trip_id"])
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - replay/SQL failure
        logger.exception("demo.replay: replay step failed")
        raise HTTPException(status_code=503, detail=f"replay failed: {exc}") from exc
    finally:
        conn.close()

    # --- 3. Run the pipeline inline so the injected rows are sensed ->
    #        reasoned -> acted -> published to Redis -> WS immediately. A
    #        pipeline failure here is NOT fatal to the demo: the rows are
    #        already injected and the scheduler will pick them up on its next
    #        tick, so we return the injection summary with an empty pipeline
    #        summary rather than a 5xx.
    pipeline_summary: list[dict[str, Any]] = []
    try:
        from app.graph.supervisor import run_pipeline

        pipeline_summary = run_pipeline(org_id)
        # Persist + live-publish the activity rows the same way a scheduler tick
        # does (schedulers/interval.py), so the Agent Activity feed updates live
        # over SSE when the demo button is pressed -- run_pipeline itself does
        # not record activity; the scheduler normally does that separately.
        try:
            from app.services.activity_log import record_pipeline_summary

            record_pipeline_summary(org_id, pipeline_summary, triggered_by="event")
        except Exception:  # noqa: BLE001 - activity logging must never fail the demo
            logger.exception("demo.replay: activity record/publish failed (non-fatal)")
    except Exception:  # noqa: BLE001
        logger.exception("demo.replay: run_pipeline failed (rows injected, scheduler will retry)")

    return ReplayResponse(
        scenario=body.scenario,
        org_id=org_id,
        injected_trip_ids=injected,
        new_trip_ids=new_trip_ids,
        pipeline_summary=pipeline_summary,
    )
