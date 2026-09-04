"""Shared Postgres checkpointer factory (plan §4: "Checkpointer: Postgres
(`PostgresSaver`) -- not Redis, not in-memory -- for durability across
restarts and a queryable 'what did the agent see/decide' trail").

Every compiled graph that needs `interrupt()`/`Command(resume=...)` to
survive a process restart -- critically `build_act_subgraph(checkpointer=...)`
(backend/app/graph/act/subgraph.py) -- must be compiled with the *same*
checkpointer instance this module returns, so a paused thread started by one
process (e.g. the scheduler) can be resumed by another (the FastAPI backend).

API surface confirmed against the installed langgraph-checkpoint-postgres
version (see backend/app/graph/act/requirements.txt's `>=2.0` pin; verified
live here against 3.1.2 -- the same "check the installed version's actual
API" approach backend/app/memory/ is expected to follow, per this module's
own build instructions): `PostgresSaver(conn)` takes a psycopg `Connection`
*or* `ConnectionPool`, and `.setup()` is a synchronous, idempotent method that
creates/migrates the `checkpoints`/`checkpoint_writes`/... tables -- it must
run once before the checkpointer is used, but re-running it on an
already-migrated DB is a no-op (safe to call on every process start).

A `ConnectionPool` (not a single `Connection`) is used here because this
checkpointer is shared across concurrent graph invocations (the scheduler's
interval tick and its event-listener path can both be mid-`.invoke()` at
once) -- a single `Connection` is not safe for concurrent use, a pool is.
`autocommit=True` + `row_factory=dict_row` mirror the exact kwargs
PostgresSaver's own docs/tests configure a pool with; the checkpoint tables
do their own transaction management per call.
"""

from __future__ import annotations

import functools
import os

from langgraph.checkpoint.postgres import PostgresSaver
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

_DEFAULT_MAX_POOL_SIZE = 20


def _normalize_pg_url(database_url: str) -> str:
    """psycopg (v3) accepts a bare `postgresql://` DSN directly -- unlike the
    SQLAlchemy engines elsewhere in this codebase (sql_agent/subgraph.py,
    act/db.py), which need the `+psycopg` dialect prefix rewritten in. No
    rewrite needed here; kept as a named function anyway so a future scheme
    change only needs editing in one place."""
    return database_url


@functools.lru_cache(maxsize=1)
def get_checkpointer(database_url: str | None = None, *, max_size: int = _DEFAULT_MAX_POOL_SIZE) -> PostgresSaver:
    """Returns the process-wide singleton `PostgresSaver`, running `.setup()`
    on first construction. Cached so every caller in a process (supervisor,
    scheduler, and -- later -- the FastAPI app) shares one pool/checkpointer
    instance rather than each opening its own connections.
    """
    url = _normalize_pg_url(database_url or os.environ["DATABASE_URL"])
    pool = ConnectionPool(
        conninfo=url,
        max_size=max_size,
        kwargs={"autocommit": True, "row_factory": dict_row},
        open=True,
    )
    checkpointer = PostgresSaver(pool)
    checkpointer.setup()
    return checkpointer
