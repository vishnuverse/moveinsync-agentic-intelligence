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

from app.graph.supervisor import run_pipeline

logger = logging.getLogger(__name__)

DEFAULT_INTERVAL_MINUTES = 5


def _tick(org_id: str) -> None:
    try:
        summary = run_pipeline(org_id)
        paused = sum(1 for entry in summary if entry.get("paused"))
        logger.info(
            "scheduler tick: org=%s dispatches=%d paused_for_signoff=%d", org_id, len(summary), paused
        )
    except Exception:  # noqa: BLE001 - one bad tick must not kill the recurring job
        logger.exception("scheduler tick failed for org %s", org_id)


def build_interval_scheduler(org_id: str, *, interval_minutes: int | None = None) -> AsyncIOScheduler:
    """Returns an unstarted `AsyncIOScheduler` with one recurring job wired to
    `run_pipeline(org_id)`. The first run fires immediately (`next_run_time=
    now`) rather than waiting a full interval -- matters for both the demo
    ("start the scheduler, see it act without anyone clicking anything") and
    verification (plan step 12 verification #4: "start briefly, confirm one
    tick"). Caller is responsible for `.start()`/`.shutdown()`."""
    minutes = interval_minutes or int(os.environ.get("PIPELINE_INTERVAL_MINUTES", DEFAULT_INTERVAL_MINUTES))
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
    return scheduler
