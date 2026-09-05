"""Sense subgraph assembly (plan §4 Sense subgraph).

`poll_or_event_entry` normalizes whichever entry mode the caller used (a
scheduler's `since` delta window, or a `event` dict from the LISTEN/NOTIFY
listener in listener.py) into a concrete `since`, then all six detectors run
as parallel branches of one LangGraph `StateGraph`, and their `signals` lists
merge via the additive reducer declared on `SenseState.signals`.

This is a real LangGraph subgraph (not just a plain function) so the
top-level supervisor/reason/act graphs built by other agents can compose it
uniformly -- `build_sense_subgraph().compile()` returns a `Runnable` like any
other node/subgraph in the top-level graph.
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph
from sqlalchemy import Engine, text

from app.contracts import get_contract
from app.graph.sense import nodes as detectors
from app.graph.sense.db import get_engine
from app.graph.sense.state import SenseState

_DETECTOR_NAMES = (
    "detect_delay_signal",
    "detect_incident_signal",
    "detect_cost_anomaly",
    "detect_emissions_signal",
    "detect_attendance_correlation",
    "detect_escort_compliance_signal",
    "detect_billing_discrepancy_signal",
    "detect_variability_signal",
    "flag_data_quality",
)

# SP-B (plan §2c): maps each detector's own kwarg names to the `alert_rules`
# signal_type whose `params` blob can override them, so
# `_make_detector_node` below can resolve config-driven thresholds without
# any detector function itself changing its (pure, testable) signature
# beyond the small additive params already added in nodes.py. A detector not
# listed here (flag_data_quality) is never rules-driven -- it always runs
# with its defaults.
_DETECTOR_KWARGS: dict[str, tuple[str, ...]] = {
    "detect_delay_signal": ("delay_threshold_minutes",),
    "detect_incident_signal": ("severity_threshold",),
    "detect_cost_anomaly": ("divergence_pct",),
    "detect_emissions_signal": ("min_ratio_over_baseline",),
    "detect_attendance_correlation": (
        "delay_threshold_minutes",
        "min_late_samples",
        "signal_limit",
        "transport_correlation_ratio",
        "unrelated_correlation_ratio",
    ),
    "detect_escort_compliance_signal": (
        "violation_limit",
        "night_window_start_hour",
        "night_window_end_hour",
        "drop_delay_critical_minutes",
    ),
    "detect_billing_discrepancy_signal": ("min_slab_sample", "min_discrepancy_inr"),
    "detect_variability_signal": ("cv_threshold_pct", "min_sample_size", "variability_minutes_floor"),
}
_RULES_KEY_BY_DETECTOR: dict[str, str] = {
    "detect_delay_signal": "delay_breach",
    "detect_incident_signal": "incident",
    "detect_cost_anomaly": "cost_divergence",
    "detect_emissions_signal": "emissions_over_target",
    # SP-B fix: was the phantom key "attendance_correlation" -- no Signal is
    # ever emitted with that signal_type, so gate.py's per-signal rules
    # lookup (keyed on the real Signal.signal_type) could never see these
    # params' gate_mode/cadence, and the Settings page rendered a 3rd,
    # functionally-dead card alongside the two real ones. Using the actual
    # dispatched signal_type as the params key matches every other detector
    # here (e.g. "delay_breach" is both the params key and a real signal_type).
    "detect_attendance_correlation": "attendance_correlated_with_transport",
    "detect_escort_compliance_signal": "escort_compliance_violation",
    "detect_billing_discrepancy_signal": "billing_discrepancy",
    "detect_variability_signal": "performance_variability",
}


def poll_or_event_entry(state: SenseState) -> dict:
    """Resolves `org_id` when the caller didn't pass one. Does not otherwise
    branch on `event` today -- every detector re-scans its own window
    regardless of which table's row triggered the run, since one changed row
    can be relevant to more than one detector (e.g. a new trip affects delay
    AND attendance correlation AND cost, once its cost/emissions rows land).

    BUGFIX (found live: run_sense() returned only data_quality_issue against
    the real dataset, even after nodes.py's `_resolve_since` was fixed to
    anchor on the data's own most-recent activity instead of wall-clock
    `now`): this function used to ALSO fill in a `since` default here, via
    `datetime.now(timezone.utc) - DEFAULT_ENTRY_LOOKBACK` -- a second,
    earlier, wall-clock-anchored resolution that ran before any detector, so
    every detector received an already-concrete (and wrong, for real data)
    `since` and never hit nodes.py's `_resolve_since(since=None)` branch at
    all. Leaving `since` unset here when the caller didn't pass one is the
    fix -- each detector calls the real, data-anchored `_resolve_since`
    itself, per its own org's actual data."""

    org_id = state.get("org_id")
    if not org_id:
        from app.contracts import get_contract

        org_id = get_contract().default_org_id

    return {"org_id": org_id}


def _real_data_available(conn) -> bool:
    """Every detector ultimately reads `mis.*` (via app.contracts), which
    db/real_data/ingest.py only creates when the real dataset's CSVs are
    present (see docker-compose.yml's `seed` comment) -- a fresh clone
    without that gitignored, host-only dataset never gets `mis.trip`
    created. Gated once here, on the subgraph's single shared entry point,
    rather than in each detector: every detector queries `mis.*` more than
    once (nodes.py's `_resolve_since` guard only covers its own first
    lookup), so checking per-query would still let a detector's second or
    third query raise UndefinedTable past `safe_detect` and spam a full
    traceback into the logs on every scheduler tick."""
    trip = get_contract().entity("trip")
    return conn.execute(text("SELECT to_regclass(:t)"), {"t": trip.table}).scalar() is not None


def _make_detector_node(detector_name: str, engine: Engine):
    detector_fn = getattr(detectors, detector_name)
    expected_kwargs = _DETECTOR_KWARGS.get(detector_name, ())
    rules_key = _RULES_KEY_BY_DETECTOR.get(detector_name)

    def node(state: SenseState) -> dict:
        org_id = state["org_id"]
        since = state.get("since")
        kwargs: dict = {}
        if rules_key and expected_kwargs:
            # SP-B (plan §2c): resolved once per detector node per tick (the
            # loader itself caches per-org for 30s, so this is cheap even
            # across the 8 parallel detector branches). Only non-None
            # overrides are passed -- everything else falls through to the
            # detector's own DEFAULT_* argument default, unchanged.
            from app.rules import get_rules

            signal_rules = get_rules(engine, org_id).get(rules_key)
            if signal_rules is not None:
                kwargs = {
                    key: signal_rules.get(key)
                    for key in expected_kwargs
                    if signal_rules.get(key) is not None
                }
        with engine.connect() as conn:
            if not _real_data_available(conn):
                return {"signals": []}
            signals = detector_fn(conn, org_id, since, **kwargs)
        return {"signals": signals}

    node.__name__ = detector_name
    return node


def build_sense_subgraph(engine: Engine | None = None) -> StateGraph:
    """Returns an uncompiled `StateGraph` -- call `.compile()` on it (with or
    without a checkpointer) before invoking. Kept uncompiled here so a caller
    composing this into a larger graph can add its own checkpointer/config at
    the outer `.compile()` call instead of being forced into this module's."""

    engine = engine or get_engine()
    graph = StateGraph(SenseState)

    graph.add_node("poll_or_event_entry", poll_or_event_entry)
    for name in _DETECTOR_NAMES:
        graph.add_node(name, _make_detector_node(name, engine))

    graph.add_edge(START, "poll_or_event_entry")
    for name in _DETECTOR_NAMES:
        graph.add_edge("poll_or_event_entry", name)
        graph.add_edge(name, END)

    return graph


def run_sense(
    org_id: str | None = None,
    since: datetime | None = None,
    event: dict | None = None,
    engine: Engine | None = None,
) -> SenseState:
    """Convenience entry point for a scheduler that just wants signals back
    without holding a compiled graph handle itself. Equivalent to
    `build_sense_subgraph(engine).compile().invoke({...})`."""

    compiled = build_sense_subgraph(engine).compile()
    initial_state: SenseState = {"signals": []}
    if org_id is not None:
        initial_state["org_id"] = org_id
    if since is not None:
        initial_state["since"] = since
    if event is not None:
        initial_state["event"] = event
    return compiled.invoke(initial_state)
