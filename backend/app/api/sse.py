"""GET /api/sse/{persona} -- Server-Sent Events mirror of the WS /api/ws/{persona}
push (plan API contract).

Same job as app/api/ws.py, over SSE instead of a WebSocket so a plain
`EventSource` (no socket library, survives proxies that drop WS upgrades) can
drive the live notification inbox AND the Agent Activity feed. Subscribes to
BOTH Redis channels:

  * notification_channel(persona) == `notifications:{persona}` -- the exact
    channel the act nodes publish to (app/graph/act/nodes.py), forwarded by
    ws.py today.
  * activity_channel(default_org_id()) == `activity:{org_id}` -- the
    system-wide pipeline-run feed published by app/services/activity_log.py's
    record_pipeline_summary/record_report_run.

Each Redis frame is forwarded verbatim as an SSE `data:` event (JSON body
unchanged, only tagged with a `kind` if a publisher somehow omitted one). A
`: keepalive` comment is emitted on every idle interval (~15s) so proxies and
load balancers don't reap the idle connection.
"""

from __future__ import annotations

import json
import logging
import os
from typing import AsyncIterator

import redis.asyncio as redis_asyncio
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from app.api.deps import default_org_id
from app.graph.act.redis_publish import activity_channel, notification_channel

logger = logging.getLogger(__name__)

router = APIRouter(tags=["sse"])

# Idle interval between keepalive comments (seconds). Doubles as the pubsub
# read timeout: a real message returns immediately, otherwise we wake to send
# a keepalive.
KEEPALIVE_SECONDS = 15.0


def _redis_url() -> str:
    return os.environ.get("REDIS_URL", "redis://localhost:6379/0")


def _tag_frame(raw: str, channel: str | None) -> str:
    """Forward a Redis frame unchanged, only injecting a `kind` if the
    publisher omitted one (every current publisher already sets it -- this is
    a safety net so a consumer can always branch on `kind`). Non-JSON or
    non-object frames are forwarded verbatim."""
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        return raw
    if isinstance(payload, dict) and "kind" not in payload:
        payload["kind"] = "activity" if (channel or "").startswith("activity:") else "notification"
        return json.dumps(payload, default=str)
    return raw


@router.get("/sse/{persona}")
async def sse_notifications(persona: str, request: Request) -> StreamingResponse:
    org_id = default_org_id()
    client = redis_asyncio.from_url(_redis_url(), decode_responses=True)
    pubsub = client.pubsub()
    channels = [notification_channel(persona), activity_channel(org_id)]
    await pubsub.subscribe(*channels)

    async def event_stream() -> AsyncIterator[str]:
        # An opening comment flushes response headers immediately so the
        # client's EventSource fires `onopen` without waiting for the first
        # real event.
        yield ": connected\n\n"
        try:
            while True:
                if await request.is_disconnected():
                    break
                message = await pubsub.get_message(
                    ignore_subscribe_messages=True, timeout=KEEPALIVE_SECONDS
                )
                if message is None:
                    yield ": keepalive\n\n"
                    continue
                if message.get("type") != "message":
                    continue
                yield f"data: {_tag_frame(message['data'], message.get('channel'))}\n\n"
        except Exception:  # noqa: BLE001 - never let a bad frame crash the stream/process
            logger.exception("sse_notifications: error on channels %s", channels)
        finally:
            try:
                await pubsub.unsubscribe(*channels)
                await pubsub.close()
            finally:
                await client.close()

    headers = {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        # Disable proxy buffering (nginx) so events flush as they're produced.
        "X-Accel-Buffering": "no",
    }
    return StreamingResponse(event_stream(), media_type="text/event-stream", headers=headers)
