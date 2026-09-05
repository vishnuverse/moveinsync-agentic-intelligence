"""Notification-cadence scheduling helper (plan SP-B §3).

Pure function, no I/O: `compute_scheduled_for` maps a signal_type's
configured `notification_cadence` to the timestamp at which the resulting
`agent_notifications` row should first become visible. `None` means
"immediate" (today's existing behavior, unchanged) -- app.services.
notifications_query's list_notifications/count_notifications treat
`scheduled_for IS NULL OR scheduled_for <= now()` as visible.

This is deliberately a per-notification VISIBILITY DELAY, not a merged
digest: see plan §3's explicit non-goal note. All boundaries are computed in
UTC, matching how the rest of the pipeline already treats timestamps.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

VALID_CADENCES = ("immediate", "hourly", "every_2_hours", "daily", "weekly")


def compute_scheduled_for(cadence: str | None, now: datetime) -> datetime | None:
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    if cadence is None or cadence == "immediate":
        return None

    if cadence == "hourly":
        base = now.replace(minute=0, second=0, microsecond=0)
        return base + timedelta(hours=1)

    if cadence == "every_2_hours":
        base = now.replace(minute=0, second=0, microsecond=0)
        hours_to_next_even = 2 - (base.hour % 2)
        return base + timedelta(hours=hours_to_next_even)

    if cadence == "daily":
        base = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return base + timedelta(days=1)

    if cadence == "weekly":
        base = now.replace(hour=0, minute=0, second=0, microsecond=0)
        days_to_next_monday = (7 - base.weekday()) % 7
        days_to_next_monday = days_to_next_monday or 7
        return base + timedelta(days=days_to_next_monday)

    raise ValueError(f"unknown notification_cadence '{cadence}' -- expected one of {VALID_CADENCES}")
