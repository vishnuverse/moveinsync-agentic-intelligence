import type { ChartComparison, ChartContributor } from "../api";
import "./charts.css";

interface ComparisonBadgeProps {
  comparison?: ChartComparison;
  suffix?: string;
}

/** Small "vs previous period" delta pill -- deliberately neutral (arrow +
 * magnitude, no green/red good/bad coloring) since whether a delta is good
 * news depends on the metric (e.g. rising cost is bad, rising SLA is good)
 * and this component has no way to know which. */
export function ComparisonBadge({ comparison, suffix = "" }: ComparisonBadgeProps) {
  if (!comparison) return null;
  const arrow = comparison.delta_pct > 0 ? "↑" : comparison.delta_pct < 0 ? "↓" : "→";
  return (
    <div className="chart-comparison-badge">
      <span className="chart-comparison-arrow">{arrow}</span>
      {comparison.previous_value.toFixed(1)}
      {suffix} <span className="chart-comparison-sep">&rarr;</span> {comparison.current_value.toFixed(1)}
      {suffix}
      <span className="chart-comparison-label">{comparison.label}</span>
    </div>
  );
}

interface ContributorsListProps {
  contributors?: ChartContributor[];
  title?: string;
}

/** Companion "who's responsible" list for a benchmark/aggregate chart --
 * e.g. billing discrepancy's PRD framing is "two vendors are responsible for
 * the gap", so the chart needs to name them, not just show the trend. */
export function ContributorsList({ contributors, title = "Top contributors" }: ContributorsListProps) {
  if (!contributors || contributors.length === 0) return null;
  return (
    <div className="chart-contributors">
      <div className="chart-contributors-title">{title}</div>
      <ul>
        {contributors.map((c) => (
          <li key={c.name}>
            <span className="chart-contributors-name">{c.name}</span>
            <span className="chart-contributors-pct">{c.pct.toFixed(0)}%</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
