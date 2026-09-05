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
from app.api.schemas import PersonaId, ReportGenerateRequest, ReportMeta
from app.services.reports_query import get_report, list_reports

router = APIRouter(tags=["reports"])

# Persona -> its default report_type when POST /reports/generate omits one.
# Inverse of supervisor.REPORT_TYPE_PERSONA (which is report_type -> persona);
# kept as an explicit literal here so a bad/removed mapping fails loudly at
# request time rather than silently generating the wrong report_type.
_PERSONA_DEFAULT_REPORT_TYPE: dict[str, str] = {
    "transport_manager": "daily_digest",
    "line_manager": "weekly_digest",
    "transport_head": "monthly_leadership",
}


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
    return [_report_meta(row) for row in rows]


def _report_meta(row: dict) -> ReportMeta:
    base = public_api_base_url()
    return ReportMeta(
        id=str(row["id"]),
        title=row["title"],
        period=_period_label(row),
        generated_at=row["generated_at"].isoformat(),
        preview_url=f"{base}/reports/{row['id']}/html",
    )


@router.post("/reports/generate", response_model=ReportMeta)
def generate_report(body: ReportGenerateRequest) -> ReportMeta:
    """Synchronously run the act subgraph's report path for `persona` and
    return the freshly-created report's meta -- same shape as one element of
    GET /reports. Runs the LLM narrative inline, so this takes a few seconds.

    run_report() is imported lazily (matching demo.py): it pulls in the
    supervisor/graph stack, which shouldn't be paid at module-import time.
    """
    report_type = body.report_type or _PERSONA_DEFAULT_REPORT_TYPE.get(body.persona)
    if not report_type:
        raise HTTPException(status_code=422, detail=f"no default report_type for persona '{body.persona}'")

    org_id = default_org_id()
    try:
        from app.graph.supervisor import run_report

        result = run_report(org_id, body.persona, report_type=report_type)
    except Exception as exc:  # noqa: BLE001 - surface as a clean 502, don't leak a stack trace
        raise HTTPException(status_code=502, detail=f"report generation failed: {exc}") from exc

    # Prefer the exact row run_report just wrote (by id); fall back to the
    # newest row for this persona/type if the subgraph didn't return an id
    # (e.g. dispatch held for sign-off before the report row landed).
    report_id = result.get("report_id")
    row = None
    if report_id is not None:
        try:
            row = get_report(int(report_id))
        except (TypeError, ValueError):
            row = None
    if row is None:
        candidates = [r for r in list_reports(org_id, body.persona) if r.get("report_type") == report_type]
        row = candidates[0] if candidates else None
    if row is None:
        raise HTTPException(status_code=502, detail="report generation completed but no report row was found")

    return _report_meta(row)


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
