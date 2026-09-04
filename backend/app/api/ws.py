"""WS /api/ws/{persona} -- live push for the notification inbox (plan §4's
"Resume responsiveness" note: "pushes the result over the same Redis
pub/sub -> WebSocket channel already used for notifications -- the UI
updates instantly on approval").

Subscribes to the exact Redis channel app/graph/act/redis_publish.py already
publishes to (`notifications:{persona}`, written by notification_dispatch/
interrupt_gate/send_dispatch) and forwards every message verbatim as a JSON
text frame. The current frontend build (frontend/src/api/*, ChatPanel/
NotificationInbox) does not open this socket yet -- it still works via
polling/refetch after resumeNotification() resolves -- this endpoint is
correct and ready for a follow-up frontend change to consume; not wiring
frontend/ itself is intentional (constraints: don't touch frontend/ source).
"""

from __future__ import annotations

import asyncio
import logging
import os

import redis.asyncio as redis_asyncio
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.graph.act.redis_publish import notification_channel

logger = logging.getLogger(__name__)

router = APIRouter(tags=["ws"])


def _redis_url() -> str:
    return os.environ.get("REDIS_URL", "redis://localhost:6379/0")


@router.websocket("/ws/{persona}")
async def ws_notifications(websocket: WebSocket, persona: str) -> None:
    await websocket.accept()
    client = redis_asyncio.from_url(_redis_url(), decode_responses=True)
    pubsub = client.pubsub()
    channel = notification_channel(persona)
    await pubsub.subscribe(channel)

    async def forward() -> None:
        async for message in pubsub.listen():
            if message.get("type") == "message":
                await websocket.send_text(message["data"])

    async def watch_disconnect() -> None:
        # Drains/ignores any client-sent frames; this socket is server-push
        # only. Exists purely so a client disconnect (WebSocketDisconnect on
        # receive) is detected promptly instead of leaking the forward task
        # and the Redis subscription forever.
        while True:
            await websocket.receive_text()

    forward_task = asyncio.create_task(forward())
    watch_task = asyncio.create_task(watch_disconnect())
    try:
        await asyncio.wait({forward_task, watch_task}, return_when=asyncio.FIRST_COMPLETED)
    except WebSocketDisconnect:
        pass
    except Exception:  # noqa: BLE001 - never let a bad frame kill the process
        logger.exception("ws_notifications: error on channel %s", channel)
    finally:
        for task in (forward_task, watch_task):
            if not task.done():
                task.cancel()
        try:
            await pubsub.unsubscribe(channel)
            await pubsub.close()
        finally:
            await client.close()
