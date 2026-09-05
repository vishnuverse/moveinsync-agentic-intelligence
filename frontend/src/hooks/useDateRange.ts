import { useEffect, useMemo, useState } from "react";
import { api } from "../api";
import type { DataCoverage, DateRange } from "../api";

const PRESET_DAYS = [7, 30, 90] as const;

function isoDaysBefore(isoDate: string, days: number): string {
  const d = new Date(`${isoDate}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() - days);
  return d.toISOString().slice(0, 10);
}

/** Drives every persona dashboard's sliding date-range control.
 *
 * BUGFIX this hook exists to prevent regressing: every chart used to default
 * to "last N days back from wall-clock now" (or, after an earlier fix,
 * "last N days back from the data's literal most-recent row") -- both break
 * once a sparse, disconnected live-replay tail becomes the newest row (see
 * backend/app/services/date_window.py's docstring: real historical bulk data
 * can end 5+ weeks before that trickle). Defaulting here to `dense_end_date`
 * (the coverage endpoint's "most recent date with real volume") instead of
 * `end_date` means first paint always shows meaningful data; the range is
 * still fully user-slidable across the true [start_date, end_date] span,
 * live tail included, via the custom date inputs. */
export function useDateRange(defaultDays: (typeof PRESET_DAYS)[number] = 30) {
  const [coverage, setCoverage] = useState<DataCoverage | null>(null);
  const [days, setDaysState] = useState<number | null>(defaultDays);
  const [range, setRangeState] = useState<DateRange | null>(null);

  useEffect(() => {
    let cancelled = false;
    api.getDataCoverage().then((c) => {
      if (cancelled) return;
      setCoverage(c);
      const anchor = c.dense_end_date ?? c.end_date;
      if (anchor) {
        setRangeState({ since: isoDaysBefore(anchor, defaultDays), until: anchor });
      }
    });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function setPresetDays(n: number) {
    setDaysState(n);
    const anchor = coverage?.dense_end_date ?? coverage?.end_date;
    if (anchor) setRangeState({ since: isoDaysBefore(anchor, n), until: anchor });
  }

  function setCustomRange(since: string, until: string) {
    setDaysState(null);
    setRangeState({ since, until });
  }

  const bounds = useMemo(
    () => ({ min: coverage?.start_date ?? undefined, max: coverage?.end_date ?? undefined }),
    [coverage],
  );

  return {
    coverage,
    range,
    days,
    bounds,
    presetDays: PRESET_DAYS,
    setPresetDays,
    setCustomRange,
    ready: range !== null,
  };
}
