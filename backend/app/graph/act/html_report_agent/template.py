"""Static/templated HTML rendering for the flagship report deliverable (plan
§2 html_report_generator, §11 backend/app/graph/act/html_report_agent/).

Everything here is inline CSS in one standalone <html> document -- no
external stylesheet, font, or script tag, no network call -- so the output
opens correctly as a single .html file with no dependencies, per the build
brief. Brand colors are taken verbatim from docs/brandguidelines/
brandguidelines.md: Apple #38AF48 (primary), Malibu #8ED1FC (secondary --
backgrounds/borders/highlights only, never text, per the guideline's own
contrast note), Outer Space #32373C (dark text/headings), body #333333,
background #FFFFFF, borders #666666.

This module renders the skeleton, KPI cards, and decision table from
structured data only -- it never calls an LLM. narrative.py/static_narrative.py
independently produce the two prose paragraphs slotted into
`_narrative_section()`; either can hand this module a plain
{"exec_summary": ..., "why_it_matters": ...} dict.
"""

from __future__ import annotations

import html
from datetime import datetime
from typing import Any

APPLE_GREEN = "#38AF48"
MALIBU_BLUE = "#8ED1FC"
OUTER_SPACE = "#32373C"
BODY_TEXT = "#333333"
BACKGROUND = "#FFFFFF"
BORDER = "#666666"
MALIBU_TINT = "#EAF6FE"  # very light Malibu tint for card fills -- background use only, never text

_PERSONA_LABELS = {
    "transport_manager": "Transport Manager",
    "line_manager": "Line Manager",
    "transport_head": "Transport Head",
}


def persona_label(persona: str) -> str:
    return _PERSONA_LABELS.get(persona, persona.replace("_", " ").title())


def compute_kpis(items: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(items)
    signoff_count = sum(1 for it in items if it.get("needs_human_signoff"))
    confidences = [it["confidence"] for it in items if isinstance(it.get("confidence"), (int, float))]
    avg_confidence_pct = round(sum(confidences) / len(confidences) * 100, 1) if confidences else None

    persona_breakdown: dict[str, int] = {}
    for it in items:
        p = it.get("target_persona") or "unspecified"
        persona_breakdown[p] = persona_breakdown.get(p, 0) + 1

    return {
        "total_items": total,
        "needs_signoff_count": signoff_count,
        "avg_confidence_pct": avg_confidence_pct,
        "persona_breakdown": persona_breakdown,
    }


def _kpi_card(label: str, value: str) -> str:
    return f"""
        <div style="flex:1;min-width:160px;background:{MALIBU_TINT};border:1px solid {BORDER};
                    border-top:4px solid {MALIBU_BLUE};border-radius:6px;padding:16px;">
          <div style="font-size:12px;letter-spacing:.04em;text-transform:uppercase;color:{BODY_TEXT};opacity:.75;">
            {html.escape(label)}
          </div>
          <div style="font-size:28px;font-weight:700;color:{OUTER_SPACE};margin-top:6px;">
            {html.escape(value)}
          </div>
        </div>"""


def _kpi_cards_html(metrics: dict[str, Any]) -> str:
    avg_conf = metrics.get("avg_confidence_pct")
    cards = [
        _kpi_card("Decision Items", str(metrics["total_items"])),
        _kpi_card("Requiring Sign-off", str(metrics["needs_signoff_count"])),
        _kpi_card("Avg. Model Confidence", f"{avg_conf}%" if avg_conf is not None else "N/A"),
    ]
    return f'<div style="display:flex;gap:16px;flex-wrap:wrap;margin:24px 0;">{"".join(cards)}</div>'


def _narrative_section(narrative: dict[str, str]) -> str:
    exec_summary = html.escape(narrative.get("exec_summary", "")).replace("\n", "<br/>")
    why_it_matters = html.escape(narrative.get("why_it_matters", "")).replace("\n", "<br/>")
    return f"""
      <section style="margin:32px 0;">
        <h2 style="color:{OUTER_SPACE};font-size:18px;border-bottom:2px solid {APPLE_GREEN};padding-bottom:6px;">
          Executive Summary
        </h2>
        <p style="color:{BODY_TEXT};font-size:14px;line-height:1.6;">{exec_summary}</p>
      </section>
      <section style="margin:32px 0;">
        <h2 style="color:{OUTER_SPACE};font-size:18px;border-bottom:2px solid {APPLE_GREEN};padding-bottom:6px;">
          Why This Matters
        </h2>
        <p style="color:{BODY_TEXT};font-size:14px;line-height:1.6;">{why_it_matters}</p>
      </section>"""


def _recommended_actions_section(items: list[dict[str, Any]]) -> str:
    """Plan SP-B §9a: a distinct, scannable "Recommended Actions" block --
    deliberately a PURE function of `items`, not the LLM-authored narrative,
    so it is structurally guaranteed non-empty even when the LLM narrative
    call fails and generator.py falls back to the static narrative (there is
    no LLM failure mode for this section at all, since it never calls one).
    One bullet per item that has a recommendation, prioritizing items that
    needed sign-off, deduplicated, capped at 5 so the section stays a
    TL;DR, not a repeat of the full findings table below it."""
    seen: set[str] = set()
    bullets: list[str] = []
    ranked = sorted(items, key=lambda it: not it.get("needs_human_signoff"))
    for item in ranked:
        recommendation = item.get("recommendation")
        if not recommendation or recommendation in seen:
            continue
        seen.add(recommendation)
        bullets.append(html.escape(recommendation))
        if len(bullets) >= 5:
            break

    if not bullets:
        body = (
            f'<p style="color:{BODY_TEXT};font-size:14px;line-height:1.6;">'
            "No items this period carried a specific recommendation.</p>"
        )
    else:
        items_html = "".join(
            f'<li style="margin:6px 0;color:{BODY_TEXT};font-size:14px;line-height:1.5;">{b}</li>'
            for b in bullets
        )
        body = f'<ul style="margin:8px 0 0;padding-left:20px;">{items_html}</ul>'

    return f"""
      <section style="margin:32px 0;">
        <h2 style="color:{OUTER_SPACE};font-size:18px;border-bottom:2px solid {APPLE_GREEN};padding-bottom:6px;">
          Recommended Actions
        </h2>
        {body}
      </section>"""


def _confidence_cell(confidence: Any) -> str:
    if not isinstance(confidence, (int, float)):
        return "&mdash;"
    return f"{round(confidence * 100)}%"


def _signoff_badge(needs_signoff: Any) -> str:
    if needs_signoff:
        return (
            f'<span style="background:{MALIBU_TINT};color:{OUTER_SPACE};border:1px solid {MALIBU_BLUE};'
            f'border-radius:4px;padding:2px 8px;font-size:12px;">Pending sign-off</span>'
        )
    return (
        f'<span style="color:{APPLE_GREEN};font-size:12px;font-weight:600;">&#10003; Auto-actioned</span>'
    )


def _items_table_html(items: list[dict[str, Any]]) -> str:
    if not items:
        return f'<p style="color:{BODY_TEXT};font-size:14px;">No decision items for this period.</p>'

    rows = []
    for it in items:
        rows.append(
            f"""
            <tr>
              <td style="padding:10px 12px;border-bottom:1px solid {BORDER};font-size:13px;color:{BODY_TEXT};">
                {html.escape(it.get("summary", ""))}
              </td>
              <td style="padding:10px 12px;border-bottom:1px solid {BORDER};font-size:13px;color:{BODY_TEXT};">
                {html.escape(it.get("root_cause", "") or "&mdash;")}
              </td>
              <td style="padding:10px 12px;border-bottom:1px solid {BORDER};font-size:13px;color:{BODY_TEXT};">
                {html.escape(it.get("recommendation", "") or "&mdash;")}
              </td>
              <td style="padding:10px 12px;border-bottom:1px solid {BORDER};font-size:13px;color:{BODY_TEXT};text-align:center;">
                {_confidence_cell(it.get("confidence"))}
              </td>
              <td style="padding:10px 12px;border-bottom:1px solid {BORDER};font-size:13px;text-align:center;">
                {_signoff_badge(it.get("needs_human_signoff"))}
              </td>
            </tr>"""
        )

    header_cells = "".join(
        f'<th style="padding:10px 12px;text-align:left;font-size:12px;text-transform:uppercase;letter-spacing:.03em;">{h}</th>'
        for h in ("Finding", "Root Cause", "Recommendation", "Confidence", "Status")
    )

    return f"""
      <table style="width:100%;border-collapse:collapse;margin-top:8px;">
        <thead>
          <tr style="background:{OUTER_SPACE};color:{BACKGROUND};">
            {header_cells}
          </tr>
        </thead>
        <tbody>
          {"".join(rows)}
        </tbody>
      </table>"""


def render_report_html(
    *,
    items: list[dict[str, Any]],
    metrics: dict[str, Any],
    narrative: dict[str, str],
    persona: str,
    period_label: str,
    generated_at: datetime,
    title: str | None = None,
) -> str:
    """Builds the full standalone HTML document string. Pure function of its
    arguments -- no I/O, no LLM call -- so it is trivially safe to call
    repeatedly (idempotent by construction)."""
    report_title = title or f"MoveInSync Operations Report &mdash; {persona_label(persona)}"
    generated_at_str = generated_at.strftime("%d %b %Y, %H:%M UTC")

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>{html.escape(report_title)}</title>
</head>
<body style="margin:0;padding:0;background:{BACKGROUND};font-family:'Helvetica Neue',Arial,sans-serif;color:{BODY_TEXT};">
  <div style="max-width:860px;margin:0 auto;">

    <header style="background:{OUTER_SPACE};color:{BACKGROUND};padding:0;">
      <div style="height:6px;background:{APPLE_GREEN};"></div>
      <div style="padding:28px 32px;">
        <div style="font-size:13px;letter-spacing:.08em;text-transform:uppercase;color:{MALIBU_BLUE};font-weight:600;">
          MoveInSync &middot; Agentic Intelligence
        </div>
        <h1 style="margin:8px 0 4px;font-size:26px;color:{BACKGROUND};">
          {html.escape(report_title)}
        </h1>
        <div style="font-size:13px;color:{BACKGROUND};opacity:.85;">
          {html.escape(period_label)} &nbsp;&bull;&nbsp; Prepared for {html.escape(persona_label(persona))}
          &nbsp;&bull;&nbsp; Generated {generated_at_str}
        </div>
      </div>
    </header>

    <main style="padding:8px 32px 32px;">
      {_kpi_cards_html(metrics)}
      {_narrative_section(narrative)}
      {_recommended_actions_section(items)}

      <section style="margin:32px 0;">
        <h2 style="color:{OUTER_SPACE};font-size:18px;border-bottom:2px solid {APPLE_GREEN};padding-bottom:6px;">
          Findings &amp; Recommendations
        </h2>
        <div style="overflow-x:auto;">
          {_items_table_html(items)}
        </div>
      </section>
    </main>

    <footer style="background:{OUTER_SPACE};color:{BACKGROUND};padding:16px 32px;font-size:11px;opacity:.75;">
      Generated automatically by MoveInSync Agentic Intelligence. Figures are drawn directly from
      operational data; sign-off items are flagged for human review before any external
      communication is sent. This report is forwardable as-is.
    </footer>

  </div>
</body>
</html>"""
