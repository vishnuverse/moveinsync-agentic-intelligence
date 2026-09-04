"""Redis pub/sub publisher for act-layer events (plan §2 Act paragraph, §4
"Resume responsiveness" note).

Publishes to `notifications:{persona}` so a future WebSocket layer can push
live updates to the UI instead of the frontend polling. Uses the sync `redis`
client -- every node in this subgraph is a plain sync function (matching the
sql_agent cluster's convention, backend/app/graph/reason/sql_agent/nodes.py)
so there's no event loop to hand an async client to; swap in
`redis.asyncio` at the call site if a future async node needs it, the
channel name and payload shape are unaffected.

REDIS_URL is read from the environment only -- never logged, printed, or
included in a returned payload.
"""

from __future__ import annotations

import functools
import json
import os
from datetime import datetime, timezone
from typing import Any

import redis


def notification_channel(persona: str) -> str:
    return f"notifications:{persona}"


@functools.lru_cache(maxsize=4)
def _client(redis_url: str) -> redis.Redis:
    return redis.Redis.from_url(redis_url, decode_responses=True)


def publish_event(channel: str, payload: dict[str, Any], *, redis_url: str | None = None) -> int:
    """Publishes `payload` (JSON-serialized) to `channel`. Returns the number
    of subscribers that received it (0 if nobody is listening yet -- fire-
    and-forget, callers should not treat 0 as an error)."""
    url = redis_url or os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    client = _client(url)
    body = {**payload, "published_at": datetime.now(timezone.utc).isoformat()}
    return client.publish(channel, json.dumps(body, default=str))
