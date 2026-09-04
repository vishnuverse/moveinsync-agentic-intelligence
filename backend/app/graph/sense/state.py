"""Shared state/types for the sense subgraph (plan §4, §2 Sense paragraph).

`Signal` is the one contract between the sense layer and everything downstream
(reason-stage nodes built by another agent): it is intentionally generic --
detectors differ wildly in what they look at, but a `reason` node should never
need to know which detector produced a `Signal` or how it queried the DB.
"""

from __future__ import annotations

import operator
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Annotated, Any, TypedDict


@dataclass(frozen=True)
class Signal:
    """One anomaly/observation surfaced by a sense-layer detector.

    Fields:
        signal_type: detector-specific kind, e.g. "delay_breach", "incident",
            "cost_divergence", "emissions_over_target",
            "attendance_correlated_with_transport", "attendance_unrelated_late",
            "data_quality_issue".
        entity_type: logical data-contract entity this signal is about
            ("trip", "route", "vendor", "incident", "employee", ...).
        entity_id: primary identifier of the flagged thing, stringified so the
            type stays uniform whether the underlying PK is numeric or composite.
        severity: "low" | "medium" | "high" | "critical".
        summary: one-line, human-readable description ready to show a user or
            feed to a reasoning LLM as-is.
        raw_metric: the numeric evidence behind the summary (counts, averages,
            thresholds compared against) -- kept as a plain dict so reason-stage
            nodes can pull specific numbers without parsing `summary` text.
        org_id: tenant scope the signal was detected under.
        detected_at: when the detector produced this signal (not necessarily
            when the underlying event occurred -- see raw_metric for that).
        source: name of the detector function that produced this signal.
        context: optional extra structured detail (route_code, vendor_name,
            team_id, etc.) useful for downstream framing but not load-bearing.
    """

    signal_type: str
    entity_type: str
    entity_id: str
    severity: str
    summary: str
    raw_metric: dict[str, Any]
    org_id: str
    detected_at: datetime
    source: str
    context: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["detected_at"] = self.detected_at.isoformat()
        return d


class SenseState(TypedDict, total=False):
    """Input/output state for the sense subgraph.

    `since`/`event` are alternative entry modes (plan §4): a scheduler tick
    passes `since` (poll a delta window); the LISTEN/NOTIFY listener passes
    `event` (react to one specific changed row). `poll_or_event_entry`
    normalizes whichever arrives into a concrete `since` every detector can use.

    `signals` uses an additive reducer because the detector nodes run as
    parallel branches in the compiled graph -- each contributes its own list
    and LangGraph merges them via `operator.add` rather than one overwriting
    another.
    """

    org_id: str
    since: datetime | None
    event: dict[str, Any] | None
    signals: Annotated[list[Signal], operator.add]
    dq_issues_found: int
