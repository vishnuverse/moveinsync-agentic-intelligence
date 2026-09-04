"""Static/templated narrative mode (plan §2: "renders ... from structured
input ... using the exact brand colors ... build this first, get it right").

No LLM call, no network -- pure string formatting over the same structured
facts the LLM-narrative mode gets, so a report is always generatable even
with `use_llm_narrative=False` or when the LLM path fails/budget-exhausts.
"""

from __future__ import annotations

from typing import Any

from .template import persona_label


def generate_static_narrative(
    items: list[dict[str, Any]],
    metrics: dict[str, Any],
    persona: str,
    period_label: str,
) -> dict[str, str]:
    total = metrics["total_items"]
    signoff = metrics["needs_signoff_count"]
    avg_conf = metrics.get("avg_confidence_pct")

    conf_clause = f" Average model confidence across items was {avg_conf}%." if avg_conf is not None else ""
    exec_summary = (
        f"During {period_label}, the agentic system surfaced {total} decision "
        f"item{'s' if total != 1 else ''} in scope for the {persona_label(persona)}. "
        f"{signoff} of these required human sign-off before any communication or "
        f"escalation was sent.{conf_clause}"
    )

    top = items[0] if items else None
    if top and (top.get("recommendation") or top.get("summary")):
        focus = top.get("recommendation") or top.get("summary")
        why_it_matters = (
            f"The highest-priority item this period: {focus} Left unaddressed, findings like "
            f"this compound into SLA breaches, avoidable cost, and safety exposure -- the reasoning "
            f"behind each item is shown in the table below so it can be verified, not just trusted."
        )
    else:
        why_it_matters = (
            "No significant items were flagged during this period. This report is generated "
            "automatically regardless of finding volume, so a quiet period is itself confirmed "
            "rather than assumed."
        )

    return {"exec_summary": exec_summary, "why_it_matters": why_it_matters}
