import type { DataCoverage, DateRange } from "../api";
import "./DateRangeSelector.css";

interface DateRangeSelectorProps {
  coverage: DataCoverage | null;
  range: DateRange | null;
  days: number | null;
  presetDays: readonly number[];
  bounds: { min?: string; max?: string };
  onPresetDays: (days: number) => void;
  onCustomRange: (since: string, until: string) => void;
}

/** Sliding date-range control for every persona dashboard (plan: "sliding
 * and updating, otherwise it won't make any sense"). Presets are day-counts
 * back from the data's own dense end (see useDateRange's docstring for why
 * that anchor, not wall-clock "now" or the literal latest row, matters);
 * the two date inputs let a viewer drag the window anywhere across the real
 * data span, live-replay tail included, and every chart re-fetches on change. */
export function DateRangeSelector({
  coverage,
  range,
  days,
  presetDays,
  bounds,
  onPresetDays,
  onCustomRange,
}: DateRangeSelectorProps) {
  if (!range) return null;

  const showsLiveTail = coverage?.dense_end_date && coverage.end_date && coverage.dense_end_date !== coverage.end_date;

  return (
    <div className="date-range-selector">
      <div className="date-range-presets" role="group" aria-label="Preset window">
        {presetDays.map((n) => (
          <button
            key={n}
            type="button"
            className={`date-range-preset-btn${n === days ? " date-range-preset-btn-active" : ""}`}
            onClick={() => onPresetDays(n)}
          >
            {n}d
          </button>
        ))}
      </div>
      <div className="date-range-custom">
        <label className="date-range-field">
          <span className="date-range-field-label">From</span>
          <input
            type="date"
            className="date-range-input"
            value={range.since}
            min={bounds.min}
            max={range.until}
            onChange={(e) => onCustomRange(e.target.value, range.until)}
          />
        </label>
        <span className="date-range-arrow" aria-hidden="true">
          →
        </span>
        <label className="date-range-field">
          <span className="date-range-field-label">To</span>
          <input
            type="date"
            className="date-range-input"
            value={range.until}
            min={range.since}
            max={bounds.max}
            onChange={(e) => onCustomRange(range.since, e.target.value)}
          />
        </label>
      </div>
      {showsLiveTail && (
        <span className="date-range-live-note" title="A live demo feed keeps adding recent data past the main historical window.">
          Data through {bounds.max} available — drag "To" forward to include live activity.
        </span>
      )}
    </div>
  );
}
