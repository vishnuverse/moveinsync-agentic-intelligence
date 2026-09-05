"""build_impact_context (plan §2 Reason paragraph, task item 2).

The single reusable function every reason-stage output passes through before
reaching the act stage -- this is what makes "every metric carries context"
(the mandatory project requirement) hold uniformly, instead of every feature
reimplementing its own ad hoc framing.

Deliberately not an LLM call: trend/severity/baseline are all derivable
arithmetic/lookup, and a deterministic function is both cheaper and more
reliable than an LLM for this step. root_cause_synthesizer is where the LLM
budget for the reason stage goes.
"""

from __future__ import annotations

from typing import Any

from app.graph.reason.state import ImpactContext
from app.graph.sense.state import Signal

_TREND_FLAT_TOLERANCE_PCT = 2.0  # a swing smaller than this reads as noise, not a real trend

VALID_SEVERITIES = ("low", "medium", "high", "critical")
_DEFAULT_SEVERITY = "low"
# Magnitude-of-deviation -> severity band, used only when no Signal is
# available to supply its own domain-specific severity.
_SEVERITY_BY_MAGNITUDE_PCT = (
    (75.0, "critical"),
    (40.0, "high"),
    (15.0, "medium"),
)

# Which raw_metric key/unit/headline carries the "headline number" for each
# sense-stage signal_type (see Signal's docstring in sense/state.py for the
# full list of signal_type values). Signal types with no single natural
# quantity (incident, data_quality_issue) fall back to a label-only metric.
_SIGNAL_METRIC_SPEC: dict[str, tuple[str, str, str]] = {
    "delay_breach": ("avg_delay_minutes", "min", "avg delay"),
    "cost_divergence": ("observed_cost_per_km", "INR/km", "observed cost/km"),
    "emissions_over_target": ("avg_co2_per_passenger_km", "gCO2/pax-km", "avg emissions"),
    # Correlated case: the ratio IS the insight ("83% of lates are the shuttle's
    # fault -> don't penalise the employee"), so keep it as the headline.
    "attendance_correlated_with_transport": ("correlation_ratio", "ratio", "transport-correlation ratio"),
    # Unrelated case: the ratio is ~0 by definition, so headlining it just
    # printed "0%" on every card. The actionable number for a line manager here
    # is HOW MANY late arrivals the employee racked up that transport does NOT
    # explain -- so headline the late count instead (the "0 of N transport-
    # caused" split still lands in the business-impact sentence + root cause).
    "attendance_unrelated_late": ("late_count", "", "late arrivals (not transport-caused)"),
}


def infer_metric_from_signal(signal: Signal) -> dict[str, Any]:
    """Builds the {label, value, unit, prior_value} descriptor
    build_impact_context() expects, straight from a sense-stage Signal -- so
    the graph node in nodes.py doesn't need its own per-signal_type copy of
    this mapping."""

    entity_label = (
        signal.context.get("route_code")
        or signal.context.get("vendor_name")
        or signal.context.get("employee_name")
        or f"{signal.entity_type} {signal.entity_id}"
    )

    spec = _SIGNAL_METRIC_SPEC.get(signal.signal_type)
    if spec is None:
        return {"label": signal.summary, "value": None, "unit": "", "prior_value": None}

    key, unit, headline = spec
    value = signal.raw_metric.get(key)
    return {
        "label": f"{entity_label} {headline}",
        "value": float(value) if value is not None else None,
        "unit": unit,
        "prior_value": None,
    }


def _pct_change(value: float, reference: float) -> float | None:
    if reference == 0:
        return None
    return round((value - reference) / abs(reference) * 100, 2)


def _trend_from_prior(value: float | None, prior_value: float | None) -> tuple[str, float | None]:
    if value is None or prior_value is None:
        return "unknown", None
    pct = _pct_change(value, prior_value)
    if pct is None:
        return "unknown", None
    if pct > _TREND_FLAT_TOLERANCE_PCT:
        return "up", pct
    if pct < -_TREND_FLAT_TOLERANCE_PCT:
        return "down", pct
    return "flat", pct


def _severity_from_magnitude_pct(magnitude_pct: float | None) -> str:
    if magnitude_pct is None:
        return _DEFAULT_SEVERITY
    magnitude = abs(magnitude_pct)
    for threshold, band in _SEVERITY_BY_MAGNITUDE_PCT:
        if magnitude >= threshold:
            return band
    return _DEFAULT_SEVERITY


def _resolve_severity(signal: Signal | None, fallback_magnitude_pct: float | None) -> str:
    """Reuses Signal.severity when available -- the detector that produced it
    already applied domain-specific thresholds for this exact call, and
    re-deriving severity from generic magnitude bands would be a strictly
    worse, duplicated heuristic."""

    if signal is not None and signal.severity in VALID_SEVERITIES:
        return signal.severity
    return _severity_from_magnitude_pct(fallback_magnitude_pct)


def _fmt_number(value: float) -> str:
    if abs(value) >= 1000:
        return f"{value:,.0f}"
    text = f"{value:,.2f}".rstrip("0").rstrip(".")
    return text or "0"


def _business_impact_sentence(
    *,
    label: str,
    value: float | None,
    unit: str,
    trend_direction: str,
    trend_pct: float | None,
    benchmark: dict[str, Any] | None,
    signal: Signal | None,
    severity_band: str,
) -> str:
    if value is None:
        headline = label
    else:
        quantity = f"{_fmt_number(value)} {unit}".strip() if unit else _fmt_number(value)
        headline = f"{label} is {quantity}"

    clauses = [headline]

    if trend_direction in ("up", "down") and trend_pct is not None:
        clauses.append(f"{trend_direction} {abs(trend_pct):.0f}% vs. the prior period")
    elif trend_direction == "flat":
        clauses.append("flat vs. the prior period")

    if benchmark is not None:
        verdict = benchmark.get("verdict")
        delta = benchmark.get("delta_pct")
        bench_value = benchmark.get("benchmark_value")
        if verdict == "above_target" and delta is not None and bench_value is not None:
            clauses.append(f"{abs(delta):.0f}% above the {_fmt_number(bench_value)} {unit} benchmark".strip())
        elif verdict == "below_target" and delta is not None and bench_value is not None:
            clauses.append(f"{abs(delta):.0f}% below the {_fmt_number(bench_value)} {unit} benchmark".strip())
        elif verdict == "within_range" and bench_value is not None:
            clauses.append(f"within the {_fmt_number(bench_value)} {unit} benchmark range".strip())

    sentence = ", ".join(clauses)
    sentence = f"{sentence[0].upper()}{sentence[1:]}" if sentence else sentence
    sentence += f" -- {severity_band} severity."

    driver_hint = None
    if signal is not None:
        driver_hint = signal.context.get("vendor_name") or signal.context.get("route_name")
    if driver_hint and driver_hint not in sentence:
        sentence = sentence[:-1] + f", tied to {driver_hint}."

    return sentence


def build_impact_context(
    metric: dict[str, Any],
    signal: Signal | None = None,
    benchmark: dict[str, Any] | None = None,
) -> ImpactContext:
    """Attach trend direction, severity band, business-impact framing, and a
    comparison baseline to one metric (task item 2).

    Args:
        metric: a light, uniform descriptor any reason-stage producer can
            build without knowing about the others --
            {"label": str, "value": float | None, "unit": str,
             "prior_value": float | None}. `value`/`prior_value` may be None
            for qualitative signals (e.g. an incident) where no single
            trend-able number exists.
        signal: the originating sense-stage Signal, if any -- its severity is
            reused rather than re-derived when present (see _resolve_severity).
        benchmark: a research_agent.ResearchComparison-shaped dict, if an
            external benchmark was looked up for this metric.
    """

    label = metric.get("label") or (signal.summary if signal is not None else "metric")
    raw_value = metric.get("value")
    value = float(raw_value) if raw_value is not None else None
    unit = metric.get("unit") or ""
    raw_prior = metric.get("prior_value")
    prior_value = float(raw_prior) if raw_prior is not None else None

    trend_direction, trend_pct = _trend_from_prior(value, prior_value)

    benchmark_delta_pct = (
        float(benchmark["delta_pct"]) if benchmark and benchmark.get("delta_pct") is not None else None
    )
    severity_band = _resolve_severity(
        signal, benchmark_delta_pct if benchmark_delta_pct is not None else trend_pct
    )

    if benchmark is not None:
        comparison_baseline: dict[str, Any] = {
            "kind": "benchmark",
            "benchmark_value": benchmark.get("benchmark_value"),
            "verdict": benchmark.get("verdict"),
            "delta_pct": benchmark.get("delta_pct"),
            "benchmark_source": benchmark.get("benchmark_source"),
        }
    elif prior_value is not None:
        comparison_baseline = {"kind": "prior_period", "prior_value": prior_value, "pct_change": trend_pct}
    elif signal is not None and signal.raw_metric:
        comparison_baseline = {"kind": "signal_raw_metric", "raw_metric": signal.raw_metric}
    else:
        comparison_baseline = {"kind": "none"}

    business_impact = _business_impact_sentence(
        label=label,
        value=value,
        unit=unit,
        trend_direction=trend_direction,
        trend_pct=trend_pct,
        benchmark=benchmark,
        signal=signal,
        severity_band=severity_band,
    )

    return ImpactContext(
        trend_direction=trend_direction,
        severity_band=severity_band,
        business_impact=business_impact,
        comparison_baseline=comparison_baseline,
    )
