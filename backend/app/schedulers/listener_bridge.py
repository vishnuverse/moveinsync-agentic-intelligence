"""Bridges `app.graph.sense.listener.SenseEventListener`'s async Postgres
LISTEN/NOTIFY stream to `app.graph.supervisor.run_pipeline` -- the
event-driven autonomy path (plan §4: "Postgres LISTEN/NOTIFY ... trigger runs
on data change"), independent of interval.py's poll path.

`run_pipeline` is synchronous/blocking (LangGraph `.invoke()` under the
hood); it's dispatched via `loop.run_in_executor` here so a slow pipeline run
never blocks this coroutine from draining the listener's queue (the listener
itself already warns and drops on a full queue rather than blocking -- see
its own docstring -- so a slow consumer here would otherwise start losing
events).
"""

from __future__ import annotations

import asyncio
import logging

from app.graph.sense.listener import SenseEventListener
from app.graph.supervisor import run_pipeline

logger = logging.getLogger(__name__)


async def run_listener_bridge(listener: SenseEventListener, org_id: str) -> None:
    """Starts `listener` and consumes its event stream forever, calling
    `run_pipeline(org_id, event=event)` for each one. Runs until cancelled --
    the caller (main.py) owns the task lifecycle and calls `listener.stop()`
    on shutdown."""
    listener.start()
    loop = asyncio.get_running_loop()
    async for event in listener.stream():
        logger.info("event-triggered pipeline run: org=%s event=%s", org_id, event)
        try:
            await loop.run_in_executor(None, run_pipeline, org_id, None, event)
        except Exception:  # noqa: BLE001 - one bad event must not kill the listener loop
            logger.exception("event-triggered run_pipeline failed for event %s", event)
