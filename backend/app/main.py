"""FastAPI entrypoint (plan §11): wires every router in app/api/ under the
`/api` prefix -- matching frontend/src/api/apiClient.ts's BASE_URL default
(`import.meta.env.VITE_API_BASE_URL ?? "/api"`) plus each route's own path
(e.g. `/roles` -> `/api/roles`) exactly.

Run with: uvicorn app.main:app --reload --port 8000
"""

from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import activity, charts, chat, dashboard, demo, insights, meta, notifications, reports, roles, settings, sse, trace, ws

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

app = FastAPI(title="MoveInSync Agentic Intelligence API", version="1.0.0")

# Permissive local-dev CORS (Vite default origin + local docker-compose
# networking, whose final service names aren't known yet -- plan §9/§11): no
# login exists anywhere in this system by design, so there's no session
# cookie to protect with a stricter allowlist; allow_credentials stays False
# so a wildcard origin is spec-valid.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(roles.router, prefix="/api")
app.include_router(dashboard.router, prefix="/api")
app.include_router(charts.router, prefix="/api")
app.include_router(notifications.router, prefix="/api")
app.include_router(trace.router, prefix="/api")
app.include_router(reports.router, prefix="/api")
app.include_router(chat.router, prefix="/api")
app.include_router(activity.router, prefix="/api")
app.include_router(demo.router, prefix="/api")
app.include_router(meta.router, prefix="/api")
app.include_router(ws.router, prefix="/api")
app.include_router(sse.router, prefix="/api")
app.include_router(settings.router, prefix="/api")
app.include_router(insights.router, prefix="/api")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
