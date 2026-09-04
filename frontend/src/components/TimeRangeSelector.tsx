import "./TimeRangeSelector.css";

interface TimeRangeSelectorProps {
  value: number;
  onChange: (days: number) => void;
  options?: number[];
}

export function TimeRangeSelector({ value, onChange, options = [7, 30, 90] }: TimeRangeSelectorProps) {
  return (
    <div className="time-range-selector" role="group" aria-label="Time range">
      {options.map((days) => (
        <button
          key={days}
          type="button"
          className={`time-range-btn${days === value ? " time-range-btn-active" : ""}`}
          onClick={() => onChange(days)}
        >
          {days}d
        </button>
      ))}
    </div>
  );
}
