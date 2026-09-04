"""Entry point for the `scheduler` service (plan §9): starts BOTH autonomy
paths in one process -- interval.py's poll loop and listener_bridge.py's
Postgres LISTEN/NOTIFY event loop -- against the one seeded org for now
(plan §12 step 9: "just the one seeded org, moveinsync-demo").

A future docker-compose `scheduler` service just runs this file:

    python -m app.schedulers.main

Env vars:
    DATABASE_URL              required (same DSN the rest of the app uses)
    ORG_ID                    defaults to the data contract's default_org_id
    PIPELINE_INTERVAL_MINUTES defaults to 5 (see interval.py)
"""

from __future__ import annotations

import asyncio
import logging
import os

from app.contracts import get_contract
from app.graph.sense.listener import SenseEventListener

from .interval import build_interval_scheduler
from .listener_bridge import run_listener_bridge

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


async def main() -> None:
    org_id = os.environ.get("ORG_ID") or get_contract().default_org_id
    database_url = os.environ["DATABASE_URL"]

    scheduler = build_interval_scheduler(org_id)
    scheduler.start()
    logger.info("interval scheduler started for org=%s", org_id)

    listener = SenseEventListener(database_url=database_url)
    try:
        await run_listener_bridge(listener, org_id)
    except asyncio.CancelledError:
        pass
    finally:
        scheduler.shutdown(wait=False)
        await listener.stop()
        logger.info("scheduler service stopped")


if __name__ == "__main__":
    asyncio.run(main())
