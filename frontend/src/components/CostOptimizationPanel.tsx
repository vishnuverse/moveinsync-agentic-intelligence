import type { CostOptimizationResponse } from "../api";
import { IconTrendDown, IconTrendFlat, IconTrendUp } from "./icons";
import "./CostOptimizationPanel.css";

const TREND_ICON = { up: IconTrendUp, down: IconTrendDown, flat: IconTrendFlat };

function formatInr(amount: number): string {
  return `₹${amount.toLocaleString("en-IN", { maximumFractionDigits: 0 })}`;
}

/** Plan SP-B §9b's cost-optimization-for-a-window capability, rendered as a
 * standalone panel (not a MetricCard) because it has no thread_id -- it's a
 * plain SQL aggregate over whatever window the persona picks, not a
 * signal-detection notification. Shares the dashboard's own date-range
 * picker (passed in via `range`) rather than owning a second one, so
 * "cost optimization for a window" and "the dashboard's window" are always
 * the same window. */
export function CostOptimizationPanel({ data }: { data: CostOptimizationResponse | null }) {
  if (!data) return null;
  const TrendIcon = TREND_ICON[data.trend_direction];

  return (
    <div className="cost-opt-panel">
      <div className="cost-opt-summary">
        <div className="cost-opt-total">
          <span className="cost-opt-total-value">{formatInr(data.window_total_inr)}</span>
          <span className="cost-opt-total-label">
            total spend, {data.window_start} → {data.window_end}
          </span>
        </div>
        {data.trend_pct !== null && data.trend_pct !== undefined && (
          <div className={`cost-opt-trend cost-opt-trend-${data.trend_direction}`}>
            <TrendIcon width={14} height={14} />
            <span>
              {Math.abs(data.trend_pct)}% {data.trend_direction === "up" ? "above" : data.trend_direction === "down" ? "below" : "vs."} the
              preceding 30-day average ({formatInr(data.baseline_avg_per_day_inr)}/day)
            </span>
          </div>
        )}
      </div>

      {data.opportunities.length > 0 ? (
        <ul className="cost-opt-opportunities">
          {data.opportunities.map((opp) => (
            <li key={opp.vendor_name} className="cost-opt-opportunity">
              <div className="cost-opt-opportunity-head">
                <span className="cost-opt-opportunity-vendor">{opp.vendor_name}</span>
                <span className="badge badge-warning">{opp.cv_pct}% inconsistent</span>
              </div>
              <p className="cost-opt-opportunity-rec">{opp.recommendation}</p>
            </li>
          ))}
        </ul>
      ) : (
        <p className="cost-opt-empty">No vendor billing inconsistencies flagged in this window.</p>
      )}
    </div>
  );
}
