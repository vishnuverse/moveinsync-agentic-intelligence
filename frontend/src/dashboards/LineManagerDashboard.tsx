import { useEffect, useState } from "react";
import { api } from "../api";
import type { ChartSeriesData, PieChartData } from "../api";
import { ChartPanel } from "../charts/ChartPanel";
import { DonutChart } from "../charts/DonutChart";
import { TrendLineChart } from "../charts/TrendLineChart";
import "../charts/charts.css";
import { DashboardShell } from "./DashboardShell";

export function LineManagerDashboard() {
  const [noShowTrend, setNoShowTrend] = useState<ChartSeriesData | null>(null);
  const [absenceSplit, setAbsenceSplit] = useState<PieChartData | null>(null);

  useEffect(() => {
    api.getNoShowTrend().then(setNoShowTrend);
    api.getAbsenceSplit().then(setAbsenceSplit);
  }, []);

  return (
    <DashboardShell
      persona="line_manager"
      heading="Team Commute Overview"
      description="How transport is affecting your team's attendance, safety, and cost — with context, not just numbers."
      charts={
        <div className="charts-grid">
          <ChartPanel title="Team No-Show Rate" subtitle="Daily % of planned bookings not boarded">
            {noShowTrend && <TrendLineChart data={noShowTrend} valueSuffix="%" />}
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
