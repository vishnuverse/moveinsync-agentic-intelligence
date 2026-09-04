"""Async Postgres LISTEN/NOTIFY eventing for the sense layer (plan §4).

Owns one dedicated, long-lived `asyncpg` connection -- kept entirely separate
from any FastAPI/SQLAlchemy request-serving pool, so a slow reconnect here
never blocks a web server's event loop. Pairs with `backend/db/triggers.sql`,
which must be applied *after* `backend/db/schema.sql` (that file isn't
touched by this module -- it's owned by the schema agent) and fires
`pg_notify('moveinsync_events', ...)` on insert into `route_trips`,
`safety_incidents`, `route_costs`, and `emissions_log`.

NOTIFY payloads are minimal by design -- `{"event": ..., "table": ...,
"id": ...}` -- well under Postgres's 8000-byte NOTIFY limit. This listener
does not try to reconstruct full row detail from the payload; a consumer
that needs it re-queries Postgres for the given (table, id).

--------------------------------------------------------------------------
Interface for a scheduler to consume (read this if you're wiring the
scheduler service that turns these events into `graph.invoke()` calls):

    listener = SenseEventListener(database_url=os.environ["DATABASE_URL"])
    listener.start()                      # returns immediately, runs in background
    ...
    async for event in listener.stream(): # event: {"event": str, "table": str, "id": int}
        await handle(event)               # e.g. graph.invoke({"event": event, ...}, config=...)
    ...
    await listener.stop()                 # graceful shutdown

`listener.events` is the underlying `asyncio.Queue[dict]` if a consumer
prefers `await listener.events.get()` over the `stream()` async generator --
both drain the same queue, use whichever fits the scheduler's own loop shape.
The queue is bounded (default 1000); if a consumer falls behind, the oldest
behavior is to drop new events with a warning log rather than grow
unbounded -- a scheduler is expected to also poll on an interval (§4), so a
dropped event is not a lost signal, just a missed early trigger for it.
--------------------------------------------------------------------------
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
from typing import AsyncIterator

import asyncpg

logger = logging.getLogger(__name__)

DEFAULT_CHANNEL = "moveinsync_events"
_INITIAL_BACKOFF_SECONDS = 1.0
_MAX_BACKOFF_SECONDS = 60.0
_HEALTHCHECK_INTERVAL_SECONDS = 1.0


class SenseEventListener:
    def __init__(
        self,
        database_url: str,
        channel: str = DEFAULT_CHANNEL,
        queue_maxsize: int = 1000,
    ) -> None:
        self._dsn = database_url
        self._channel = channel
        self.events: asyncio.Queue[dict] = asyncio.Queue(maxsize=queue_maxsize)
        self._conn: asyncpg.Connection | None = None
        self._task: asyncio.Task | None = None
        self._stop_requested = asyncio.Event()

    def start(self) -> None:
        """Schedules the listener's reconnect loop on the running event loop.
        Non-blocking -- returns as soon as the task is created, connection
        happens in the background."""

        if self._task is not None and not self._task.done():
            return
        self._stop_requested.clear()
        self._task = asyncio.create_task(self._run_forever(), name="sense-listen-notify")

    async def stop(self) -> None:
        """Signals the reconnect loop to exit and waits for it, then closes
        the underlying connection if still open."""

        self._stop_requested.set()
        if self._task is not None:
            await self._task
        if self._conn is not None and not self._conn.is_closed():
            await self._conn.close()

    async def stream(self) -> AsyncIterator[dict]:
        """Async-generator convenience wrapper over `self.events`."""

        while True:
            yield await self.events.get()

    async def _run_forever(self) -> None:
        backoff = _INITIAL_BACKOFF_SECONDS
        while not self._stop_requested.is_set():
            try:
                self._conn = await asyncpg.connect(self._dsn)
                await self._conn.add_listener(self._channel, self._on_notify)
                logger.info("sense listener connected, LISTEN %s", self._channel)
                backoff = _INITIAL_BACKOFF_SECONDS

                while not self._stop_requested.is_set():
                    if self._conn.is_closed():
                        raise ConnectionError("asyncpg connection closed unexpectedly")
                    await asyncio.sleep(_HEALTHCHECK_INTERVAL_SECONDS)

            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(
                    "sense listener lost its connection, retrying in %.1fs", backoff
                )
                if self._conn is not None and not self._conn.is_closed():
                    await self._conn.close()
                self._conn = None
                # jittered so a mass-reconnect (e.g. Postgres restart) doesn't
                # thunder-herd back in lockstep
                await asyncio.sleep(backoff + random.uniform(0, backoff * 0.25))
                backoff = min(backoff * 2, _MAX_BACKOFF_SECONDS)

        if self._conn is not None and not self._conn.is_closed():
            await self._conn.close()

    def _on_notify(self, connection: asyncpg.Connection, pid: int, channel: str, payload: str) -> None:
        try:
            event = json.loads(payload)
        except json.JSONDecodeError:
            logger.warning("dropping unparseable NOTIFY payload on %s: %r", channel, payload)
            return

        try:
            self.events.put_nowait(event)
        except asyncio.QueueFull:
            logger.warning("sense event queue full (%d), dropping event %r", self.events.qsize(), event)
