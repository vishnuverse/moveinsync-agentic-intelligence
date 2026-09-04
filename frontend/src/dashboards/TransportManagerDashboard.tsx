import { useEffect, useState } from "react";
import { api } from "../api";
import type { ChartSeriesData } from "../api";
import { BreakdownBarChart } from "../charts/BreakdownBarChart";
import { ChartPanel } from "../charts/ChartPanel";
import { TrendLineChart } from "../charts/TrendLineChart";
import "../charts/charts.css";
import { DashboardShell } from "./DashboardShell";

export function TransportManagerDashboard() {
  const [otaTrend, setOtaTrend] = useState<ChartSeriesData | null>(null);
  const [delayReasons, setDelayReasons] = useState<ChartSeriesData | null>(null);

  useEffect(() => {
    api.getOtaTrend().then(setOtaTrend);
    api.getDelayReasons().then(setDelayReasons);
  }, []);

  return (
    <DashboardShell
      persona="transport_manager"
      heading="Fleet Operations"
      description="Live SLA, cost, and safety signals for the routes and vendors you manage day to day."
      charts={
        <div className="charts-grid">
          <ChartPanel title="On-Time Arrival Rate" subtitle="Daily OTA %, breach threshold 15 min">
            {otaTrend && <TrendLineChart data={otaTrend} valueSuffix="%" />}
          </ChartPanel>
          <ChartPanel title="Delay Reason Breakdown" subtitle="Completed trips by delay cause">
            {delayReasons && <BreakdownBarChart data={delayReasons} />}
          </ChartPanel>
        </div>
      }
    />
  );
}
