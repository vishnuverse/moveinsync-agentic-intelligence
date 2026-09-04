"""Sync SQLAlchemy engine for the sense subgraph's detector queries.

Deliberately separate from whatever engine/session the FastAPI app or the
async LISTEN/NOTIFY listener use -- detectors run short-lived, synchronous
read queries triggered by a scheduler tick, not the request path.
"""

from __future__ import annotations

import functools
import os

from sqlalchemy import Engine, create_engine

_DEFAULT_DATABASE_URL = "postgresql://moveinsync:moveinsync@localhost:5432/moveinsync"


def _sqlalchemy_url(database_url: str) -> str:
    if database_url.startswith("postgresql://"):
        return database_url.replace("postgresql://", "postgresql+psycopg://", 1)
    return database_url


@functools.lru_cache(maxsize=1)
def get_engine() -> Engine:
    """Cached singleton engine, built from DATABASE_URL. Small pool -- this is
    a low-frequency polling workload, not a request-serving pool."""
    database_url = os.environ.get("DATABASE_URL", _DEFAULT_DATABASE_URL)
    return create_engine(_sqlalchemy_url(database_url), pool_pre_ping=True, pool_size=5, max_overflow=5)
