"""Per-org, per-signal-type threshold/gate-mode/cadence store backed by
public.alert_rules and public.gate_settings (plan SP-B §2). Mirrors
app.contracts.loader's shape -- sense/nodes.py detectors, chart_data.py, and
app.graph.reason.gate consult this instead of their own module-level
constants, which become fallback defaults when no row exists for an
(org_id, signal_type).

Unlike app.contracts's process-lifetime cache, this uses a short TTL: rules
are meant to be tunable from the Settings page and take effect within one
scheduler tick (default 5 min), not require a backend restart.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import Engine, text

from app.contracts import get_contract

_CACHE_TTL_SECONDS = 30.0

GateMode = str  # "auto" | "force_suppress" | "force_rule_only" | "force_escalate"
NotificationCadence = str  # "immediate" | "hourly" | "every_2_hours" | "daily" | "weekly"


@dataclass(frozen=True)
class SignalRules:
    """One (org_id, signal_type)'s configured overrides. `params` holds only
    the keys actually stored on the row -- callers merge over their own
    DEFAULT_* constants, never assume a key is present."""

    signal_type: str
    params: dict[str, Any] = field(default_factory=dict)
    gate_mode: GateMode = "auto"
    notification_cadence: NotificationCadence = "immediate"

    def get(self, key: str, default: Any = None) -> Any:
        value = self.params.get(key)
        return default if value is None else value


@dataclass(frozen=True)
class GateSettings:
    recurrence_window_hours: int = 24
    recurrence_suppress_after: int = 3
    max_consecutive_suppressions: int = 5
    rule_only_margin_ratio: float = 2.0
    max_fp_rate_for_rule_only: float = 0.20
    min_confidence_for_rule_only: float = 0.60
    max_healthy_suppression_rate: float = 0.80
    escalation_after_hours_critical: float = 1.0
    escalation_after_hours_high: float = 4.0
    escalation_after_hours_medium: float = 24.0


_DEFAULT_GATE_SETTINGS = GateSettings()

# (org_id) -> (fetched_at_monotonic, value)
_rules_cache: dict[str, tuple[float, dict[str, SignalRules]]] = {}
_gate_settings_cache: dict[str, tuple[float, GateSettings]] = {}
_cache_lock = threading.Lock()


def _fetch_rules(engine: Engine, org_id: str) -> dict[str, SignalRules]:
    contract = get_contract().entity("alert_rule")
    table, c = contract.table, contract.column
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                f"SELECT {c('signal_type')} AS signal_type, {c('params')} AS params, "
                f"{c('gate_mode')} AS gate_mode, {c('notification_cadence')} AS notification_cadence "
                f"FROM {table} WHERE {c('org_id')} = :org_id"
            ),
            {"org_id": org_id},
        ).mappings().all()

    result: dict[str, SignalRules] = {}
    for row in rows:
        params = row["params"]
        if isinstance(params, str):
            params = json.loads(params)
        result[row["signal_type"]] = SignalRules(
            signal_type=row["signal_type"],
            params=dict(params or {}),
            gate_mode=row["gate_mode"],
            notification_cadence=row["notification_cadence"],
        )
    return result


def _fetch_gate_settings(engine: Engine, org_id: str) -> GateSettings:
    contract = get_contract().entity("gate_setting")
    table, c = contract.table, contract.column
    cols = [
        "recurrence_window_hours",
        "recurrence_suppress_after",
        "max_consecutive_suppressions",
        "rule_only_margin_ratio",
        "max_fp_rate_for_rule_only",
        "min_confidence_for_rule_only",
        "max_healthy_suppression_rate",
        "escalation_after_hours_critical",
        "escalation_after_hours_high",
        "escalation_after_hours_medium",
    ]
    select_list = ", ".join(f"{c(name)} AS {name}" for name in cols)
    with engine.connect() as conn:
        row = conn.execute(
            text(f"SELECT {select_list} FROM {table} WHERE {c('org_id')} = :org_id"),
            {"org_id": org_id},
        ).mappings().first()

    if row is None:
        return _DEFAULT_GATE_SETTINGS

    kwargs = {name: row[name] for name in cols if row[name] is not None}
    # Postgres NUMERIC columns come back as Decimal -- normalize to float so
    # callers can do plain arithmetic against these without surprises.
    for key, value in list(kwargs.items()):
        if key not in ("recurrence_window_hours", "recurrence_suppress_after", "max_consecutive_suppressions"):
            kwargs[key] = float(value)
        else:
            kwargs[key] = int(value)
    return GateSettings(**kwargs)


def get_rules(engine: Engine, org_id: str) -> dict[str, SignalRules]:
    """All configured signal-type overrides for this org, keyed by
    signal_type. A signal_type with no row is simply absent from the dict --
    callers treat that as "use defaults" (see SignalRules.get's contract)."""
    now = time.monotonic()
    with _cache_lock:
        cached = _rules_cache.get(org_id)
        if cached is not None and now - cached[0] < _CACHE_TTL_SECONDS:
            return cached[1]

    value = _fetch_rules(engine, org_id)
    with _cache_lock:
        _rules_cache[org_id] = (now, value)
    return value


def get_gate_settings(engine: Engine, org_id: str) -> GateSettings:
    now = time.monotonic()
    with _cache_lock:
        cached = _gate_settings_cache.get(org_id)
        if cached is not None and now - cached[0] < _CACHE_TTL_SECONDS:
            return cached[1]

    value = _fetch_gate_settings(engine, org_id)
    with _cache_lock:
        _gate_settings_cache[org_id] = (now, value)
    return value


def invalidate_cache(org_id: str | None = None) -> None:
    """Called by the Settings PUT endpoint after a write, so a change is
    visible well within the 30s TTL rather than waiting it out."""
    with _cache_lock:
        if org_id is None:
            _rules_cache.clear()
            _gate_settings_cache.clear()
        else:
            _rules_cache.pop(org_id, None)
            _gate_settings_cache.pop(org_id, None)
