"""GET /api/reports?persona=<id> and GET /api/reports/{id}/html (plan §11).

`agent_reports.storage_ref` is a filesystem path written by
app/graph/act/html_report_agent/generator.py's html_report_generator node
(backend/data/reports/<report_type>_<persona>_<thread_id>.html) -- this
module's /html route is what actually serves that stored file, and
preview_url below points at it, per the build brief's explicit instruction
("wire a route to actually serve that stored HTML content ... set
preview_url to that").
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse

from app.api.deps import default_org_id, public_api_base_url
from app.api.schemas import PersonaId, ReportMeta
from app.services.reports_query import get_report, list_reports

router = APIRouter(tags=["reports"])


def _period_label(row: dict) -> str:
    start, end = row.get("period_start"), row.get("period_end")
    if start and end:
        return f"{start.isoformat()} to {end.isoformat()}" if hasattr(start, "isoformat") else f"{start} to {end}"
    if start:
        return start.isoformat() if hasattr(start, "isoformat") else str(start)
    return (row.get("report_type") or "ad_hoc").replace("_", " ").title()


@router.get("/reports", response_model=list[ReportMeta])
def get_reports(persona: PersonaId) -> list[ReportMeta]:
    rows = list_reports(default_org_id(), persona)
    base = public_api_base_url()
    return [
        ReportMeta(
            id=str(row["id"]),
            title=row["title"],
            period=_period_label(row),
            generated_at=row["generated_at"].isoformat(),
            preview_url=f"{base}/reports/{row['id']}/html",
        )
        for row in rows
    ]


@router.get("/reports/{report_id}/html", response_class=HTMLResponse)
def get_report_html(report_id: str) -> HTMLResponse:
    try:
        rid = int(report_id)
    except ValueError:
        raise HTTPException(status_code=404, detail=f"report '{report_id}' not found")

    row = get_report(rid)
    if row is None:
        raise HTTPException(status_code=404, detail=f"report '{report_id}' not found")

    storage_ref = row.get("storage_ref")
    path = Path(storage_ref) if storage_ref else None
    if not path or not path.exists():
        raise HTTPException(status_code=404, detail=f"report file for '{report_id}' is missing on disk")

    return HTMLResponse(content=path.read_text(encoding="utf-8"))
