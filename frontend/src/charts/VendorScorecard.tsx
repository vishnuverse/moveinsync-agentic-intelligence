import type { VendorScorecardData } from "../api";
import { TrendLineChart } from "./TrendLineChart";
import "./charts.css";

interface VendorScorecardProps {
  data: VendorScorecardData;
}

export function VendorScorecard({ data }: VendorScorecardProps) {
  return (
    <table className="vendor-scorecard-table">
      <thead>
        <tr>
          <th>Vendor</th>
          <th>SLA %</th>
          <th>Cost/km</th>
          <th>Incidents</th>
          <th>vs previous period</th>
          <th>SLA trend</th>
        </tr>
      </thead>
      <tbody>
        {data.vendors.map((v) => {
          const hasDelta = v.ontime_pct_current != null && v.ontime_pct_prev != null;
          const delta = hasDelta ? Math.round((v.ontime_pct_current! - v.ontime_pct_prev!) * 10) / 10 : null;
          return (
          <tr key={v.vendor}>
            <td className="vendor-scorecard-vendor">{v.vendor}</td>
            <td className={v.sla_pct < 92 ? "vendor-scorecard-sla-low" : undefined}>
              {v.sla_pct.toFixed(1)}%
            </td>
            <td>₹{v.cost_per_km.toFixed(2)}</td>
            <td>{v.incident_count}</td>
            <td>
              {delta != null ? (
                <span className={delta >= 0 ? "vendor-scorecard-delta-up" : "vendor-scorecard-delta-down"}>
                  {delta >= 0 ? "↑" : "↓"} {Math.abs(delta).toFixed(1)} pts
                </span>
              ) : (
                "—"
              )}
            </td>
            <td className="vendor-scorecard-sparkline-cell">
              {v.sla_trend.length > 1 && (
                <TrendLineChart
                  compact
                  data={{ categories: v.sla_trend.map((_, i) => String(i)), series: [{ name: "SLA %", data: v.sla_trend }] }}
                />
              )}
            </td>
          </tr>
          );
        })}
      </tbody>
    </table>
  );
}
