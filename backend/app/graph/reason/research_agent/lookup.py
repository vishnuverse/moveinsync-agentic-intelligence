"""External-benchmark lookup + comparison (plan §8, §4 Reason subgraph).

No live web-search API exists for this project (plan §8's confirmed
decision): this is a lookup against the curated static benchmark dataset
already seeded into `sustainability_targets`, not a web call -- deterministic,
free, and doesn't touch the LLM budget for the comparison itself.

`sustainability_targets` is referenced by its literal table name rather than
through `app.contracts.get_contract()`. This mirrors the exact precedent
already set in `app/graph/sense/nodes.py` (see its module docstring): the
table is a fixed reference/infra dataset, not a logical business entity
modeled in `data_contract.yaml`'s entity list (trip/cost/incident/emission/
...), so it stays out of the contract rather than being force-fit into it.
"""

from __future__ import annotations

import functools
import logging
import os
from dataclasses import dataclass
from typing import Any, TypedDict

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

logger = logging.getLogger(__name__)

SUSTAINABILITY_TARGETS_TABLE = "sustainability_targets"

# A deviation smaller than this reads as "on benchmark", not a real signal --
# used only as a fallback when a metric has no threshold_value of its own to
# derive a tolerance band from.
_FALLBACK_TOLERANCE_PCT = 0.05


class ResearchAgentError(Exception):
    """Raised when the requested topic/benchmark can't be resolved.

    Fails loud rather than silently guessing a comparison -- same philosophy
    as app.contracts.ContractError for an unknown entity/column.
    """


@dataclass(frozen=True)
class _TopicSpec:
    metric_name: str
    aliases: tuple[str, ...]


_TOPICS: tuple[_TopicSpec, ...] = (
    _TopicSpec(
        metric_name="cost_efficiency_inr_per_passenger_km",
        aliases=("cost", "cost_efficiency", "cost_per_km", "cost_per_passenger_km"),
    ),
    _TopicSpec(
        metric_name="sla_timeliness_pct",
        aliases=("sla", "sla_timeliness", "on_time", "on_time_pct", "timeliness", "punctuality"),
    ),
    _TopicSpec(
        metric_name="carbon_gco2_per_passenger_km",
        aliases=("carbon", "carbon_footprint", "emissions", "emission", "co2"),
    ),
)


def _resolve_topic(topic: str) -> _TopicSpec:
    normalized = topic.strip().lower().replace("-", "_").replace(" ", "_")
    for spec in _TOPICS:
        if normalized == spec.metric_name or normalized in spec.aliases:
            return spec
    known = ", ".join(sorted({spec.metric_name for spec in _TOPICS} | {a for s in _TOPICS for a in s.aliases}))
    raise ResearchAgentError(f"no curated benchmark for topic '{topic}'. Known topics/aliases: {known}")


class ResearchComparison(TypedDict):
    """The public output of run_research_agent() -- the structured comparison
    impact_context_builder consumes as its `benchmark` argument."""

    topic: str
    metric_name: str
    unit: str
    benchmark_value: float
    threshold_value: float | None
    observed_value: float
    delta_pct: float
    verdict: str  # "above_target" | "below_target" | "within_range"
    benchmark_source: str
    narrative: str | None


def _fetch_benchmark_row(conn: Connection, org_id: str, metric_name: str) -> dict[str, Any] | None:
    row = conn.execute(
        text(
            f"""
            SELECT metric_name, target_value, threshold_value, unit, period, effective_from, notes
            FROM {SUSTAINABILITY_TARGETS_TABLE}
            WHERE org_id = :org_id AND metric_name = :metric_name
            ORDER BY effective_from DESC
            LIMIT 1
            """
        ),
        {"org_id": org_id, "metric_name": metric_name},
    ).mappings().first()
    return dict(row) if row is not None else None


def _compute_verdict(observed_value: float, target_value: float, threshold_value: float | None) -> tuple[str, float]:
    """`above_target`/`below_target` are purely directional relative to
    `target_value` (not a "good"/"bad" label -- direction depends on the
    metric, e.g. above-target cost is bad but above-target SLA is good, and
    the caller/root-cause-synthesizer is what interprets that, not this
    function). The tolerance band around the target that counts as
    "within_range" comes from how far `threshold_value` sits from
    `target_value` -- e.g. cost's target=15/threshold=18 yields a
    within_range band of exactly [12, 18], recovering the seeded
    "INR 12-18/passenger-km" range from just those two numbers."""

    if threshold_value is not None and threshold_value != target_value:
        tolerance = abs(threshold_value - target_value)
    else:
        tolerance = abs(target_value) * _FALLBACK_TOLERANCE_PCT

    lower_bound = target_value - tolerance
    upper_bound = target_value + tolerance

    if observed_value > upper_bound:
        verdict = "above_target"
    elif observed_value < lower_bound:
        verdict = "below_target"
    else:
        verdict = "within_range"

    delta_pct = ((observed_value - target_value) / target_value * 100.0) if target_value else 0.0
    return verdict, round(delta_pct, 2)


def _narrate(comparison: "ResearchComparison", llm: Any) -> str:
    from langchain_core.messages import HumanMessage, SystemMessage

    if llm is None:
        from app.llm import get_chat_model

        llm = get_chat_model()

    prompt = (
        f"Observed value for '{comparison['metric_name']}' is {comparison['observed_value']} "
        f"{comparison['unit']}. The curated benchmark target is {comparison['benchmark_value']} "
        f"{comparison['unit']} (verdict: {comparison['verdict']}, delta {comparison['delta_pct']}% "
        f"vs. target). Source: {comparison['benchmark_source']}.\n"
        "Write exactly one concise, plain-language sentence a transport head could read on a "
        "dashboard, stating whether this is good or bad news and by roughly how much."
    )
    response = llm.invoke(
        [
            SystemMessage(
                content="You write single-sentence business narrative lines for a metrics dashboard. "
                "Be concrete, concise, and never invent numbers not given to you."
            ),
            HumanMessage(content=prompt),
        ]
    )
    content = response.content if isinstance(response.content, str) else str(response.content)
    return content.strip()


@functools.lru_cache(maxsize=1)
def _default_engine() -> Engine:
    from app.graph.sense.db import get_engine

    return get_engine()


def run_research_agent(
    topic: str,
    observed_value: float,
    thread_context: dict[str, Any] | None = None,
    *,
    engine: Engine | None = None,
    with_narrative: bool = False,
    llm: Any = None,
) -> ResearchComparison:
    """Look up the curated benchmark for `topic` and compare it to
    `observed_value` (plan §8, task item 1).

    A lookup + arithmetic comparison, not an LLM call -- the LLM budget is
    reserved for narrative synthesis elsewhere (root_cause_synthesizer). Pass
    with_narrative=True for an optional one-line LLM-generated wrapper around
    the comparison; off by default so a routine call never spends LLM budget.
    """

    thread_context = thread_context or {}
    org_id = thread_context.get("org_id") or _default_org_id()

    spec = _resolve_topic(topic)
    resolved_engine = engine or _default_engine()

    with resolved_engine.connect() as conn:
        row = _fetch_benchmark_row(conn, org_id, spec.metric_name)

    if row is None:
        raise ResearchAgentError(
            f"no sustainability_targets row for org_id={org_id!r}, metric_name={spec.metric_name!r} "
            "-- has backend/db/seed/generate.py been run for this org?"
        )

    target_value = float(row["target_value"])
    threshold_value = float(row["threshold_value"]) if row["threshold_value"] is not None else None
    verdict, delta_pct = _compute_verdict(observed_value, target_value, threshold_value)

    comparison = ResearchComparison(
        topic=topic,
        metric_name=spec.metric_name,
        unit=row["unit"],
        benchmark_value=target_value,
        threshold_value=threshold_value,
        observed_value=observed_value,
        delta_pct=delta_pct,
        verdict=verdict,
        benchmark_source=(
            f"sustainability_targets.{spec.metric_name} (org={org_id}, "
            f"effective_from={row['effective_from']}) -- {row['notes'] or 'curated static benchmark, plan §8'}"
        ),
        narrative=None,
    )

    if with_narrative:
        try:
            comparison["narrative"] = _narrate(comparison, llm)
        except Exception:  # noqa: BLE001 - a narrative failure must never break the (already-computed) comparison
            logger.exception("research agent narrative generation failed; returning comparison without one")

    return comparison


def _default_org_id() -> str:
    try:
        from app.contracts import get_contract

        return get_contract().default_org_id
    except Exception:  # noqa: BLE001 - fall back to the well-known demo org rather than raising here
        return os.environ.get("DEFAULT_ORG_ID", "moveinsync-demo")
