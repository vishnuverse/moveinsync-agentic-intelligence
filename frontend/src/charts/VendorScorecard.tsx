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
          <th>SLA trend</th>
        </tr>
      </thead>
      <tbody>
        {data.vendors.map((v) => (
          <tr key={v.vendor}>
            <td className="vendor-scorecard-vendor">{v.vendor}</td>
            <td className={v.sla_pct < 92 ? "vendor-scorecard-sla-low" : undefined}>
              {v.sla_pct.toFixed(1)}%
            </td>
            <td>₹{v.cost_per_km.toFixed(2)}</td>
            <td>{v.incident_count}</td>
            <td className="vendor-scorecard-sparkline-cell">
              {v.sla_trend.length > 1 && (
                <TrendLineChart
                  compact
                  data={{ categories: v.sla_trend.map((_, i) => String(i)), series: [{ name: "SLA %", data: v.sla_trend }] }}
                />
              )}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
