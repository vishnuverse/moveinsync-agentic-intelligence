"""Interval scheduler -- the "no human prompt required" poll path (plan §4:
"a scheduler service invokes graph.invoke() per monitored thread on
interval"). Independent of, and complementary to, listener_bridge.py's
LISTEN/NOTIFY event path -- both call the same
`app.graph.supervisor.run_pipeline` entry point.

Uses APScheduler's AsyncIOScheduler (plan §9's `scheduler` service description
names APScheduler explicitly) rather than a hand-rolled asyncio loop, so the
same process's event loop also runs listener_bridge.py's async LISTEN/NOTIFY
consumer without a second thread. `run_pipeline` itself is a synchronous,
blocking call (LangGraph's `.invoke()`); AsyncIOScheduler runs non-coroutine
job functions in a background thread pool executor by default, so a slow
pipeline tick does not block the listener's event loop.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.graph.supervisor import REPORT_TYPE_PERSONA, run_pipeline, run_report
from app.services.activity_log import record_pipeline_summary, record_report_run

logger = logging.getLogger(__name__)

DEFAULT_INTERVAL_MINUTES = 5

# TM4's daily digest / TH3's leadership report (plan §1) -- a real deployment
# runs these daily/monthly; a demo needs to actually observe one firing, so
# this defaults far more frequently and is still env-overridable to the real
# cadence. Deliberately a *separate*, coarser job from _tick's signal-scan
# interval above -- run_report aggregates a period of already-reasoned
# notifications, it does not re-run sense/reason per tick.
DEFAULT_REPORT_INTERVAL_MINUTES = 30


def _tick(org_id: str) -> None:
    try:
        summary = run_pipeline(org_id)
        paused = sum(1 for entry in summary if entry.get("paused"))
        logger.info(
            "scheduler tick: org=%s dispatches=%d paused_for_signoff=%d", org_id, len(summary), paused
        )
        try:
            record_pipeline_summary(org_id, summary, triggered_by="schedule")
        except Exception:  # noqa: BLE001 - activity-log persistence must never break the tick itself
            logger.exception("scheduler tick: failed to record activity log for org %s", org_id)
    except Exception:  # noqa: BLE001 - one bad tick must not kill the recurring job
        logger.exception("scheduler tick failed for org %s", org_id)


def _report_tick(org_id: str, report_type: str, persona: str) -> None:
    try:
        result = run_report(org_id, persona, report_type=report_type)
        logger.info(
            "scheduler report tick: org=%s type=%s persona=%s items=%d report_id=%s",
            org_id, report_type, persona, result.get("item_count", 0), result.get("report_id"),
        )
        try:
            record_report_run(
                org_id, persona,
                report_type=report_type,
                thread_id=result["thread_id"],
                item_count=result.get("item_count", 0),
            )
        except Exception:  # noqa: BLE001 - activity-log persistence must never break the tick itself
            logger.exception("scheduler report tick: failed to record activity log for org %s", org_id)
    except Exception:  # noqa: BLE001 - one bad report must not kill the recurring job
        logger.exception("scheduler report tick failed for org=%s type=%s", org_id, report_type)


def build_interval_scheduler(org_id: str, *, interval_minutes: int | None = None) -> AsyncIOScheduler:
    """Returns an unstarted `AsyncIOScheduler` with the signal-scan job wired
    to `run_pipeline(org_id)` plus one `run_report(org_id, ...)` job per
    report_type in REPORT_TYPE_PERSONA (TM4's daily digest, TH3's leadership
    report -- see supervisor.py's module docstring). Every job's first run
    fires immediately (`next_run_time=now`) rather than waiting a full
    interval -- matters for both the demo ("start the scheduler, see it act
    without anyone clicking anything") and verification (plan step 12
    verification #4: "start briefly, confirm one tick"). Caller is
    responsible for `.start()`/`.shutdown()`."""
    minutes = interval_minutes or int(os.environ.get("PIPELINE_INTERVAL_MINUTES", DEFAULT_INTERVAL_MINUTES))
    report_minutes = int(os.environ.get("REPORT_INTERVAL_MINUTES", DEFAULT_REPORT_INTERVAL_MINUTES))

    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        _tick,
        trigger="interval",
        minutes=minutes,
        args=[org_id],
        id=f"run_pipeline:{org_id}",
        next_run_time=datetime.now(),
        max_instances=1,
        coalesce=True,
    )
    for report_type, persona in REPORT_TYPE_PERSONA.items():
        scheduler.add_job(
            _report_tick,
            trigger="interval",
            minutes=report_minutes,
            args=[org_id, report_type, persona],
            id=f"run_report:{org_id}:{report_type}",
            next_run_time=datetime.now(),
            max_instances=1,
            coalesce=True,
        )
    return scheduler
