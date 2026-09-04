import { useEffect, useState } from "react";
import { api } from "../api";
import type { ChartSeriesData, PieChartData } from "../api";
import { ChartPanel } from "../charts/ChartPanel";
import { ComparisonBadge } from "../charts/ChartContext";
import { DonutChart } from "../charts/DonutChart";
import { TrendLineChart } from "../charts/TrendLineChart";
import "../charts/charts.css";
import { TimeRangeSelector } from "../components/TimeRangeSelector";
import { DashboardShell } from "./DashboardShell";

export function LineManagerDashboard() {
  const [days, setDays] = useState(30);
  const [noShowTrend, setNoShowTrend] = useState<ChartSeriesData | null>(null);
  const [absenceSplit, setAbsenceSplit] = useState<PieChartData | null>(null);

  useEffect(() => {
    api.getNoShowTrend(days).then(setNoShowTrend);
    api.getAbsenceSplit(days).then(setAbsenceSplit);
  }, [days]);

  return (
    <DashboardShell
      persona="line_manager"
      heading="Team Commute Overview"
      description="How transport is affecting your team's attendance, safety, and cost — with context, not just numbers."
      charts={
        <div className="charts-grid">
          <div className="charts-grid-wide chart-range-row">
            <TimeRangeSelector value={days} onChange={setDays} />
          </div>
          <ChartPanel title="Team No-Show Rate" subtitle="Daily % of planned bookings not boarded">
            {noShowTrend && (
              <>
                <TrendLineChart data={noShowTrend} valueSuffix="%" />
                <ComparisonBadge comparison={noShowTrend.comparison} suffix="%" />
              </>
            )}
          </ChartPanel>
          <ChartPanel
            title="No-Show Cause Split"
            subtitle="Delay-caused (shuttle late >15 min) vs. employee-caused"
          >
            {absenceSplit && <DonutChart data={absenceSplit} />}
          </ChartPanel>
        </div>
      }
    />
  );
}
