import { useEffect, useState } from "react";
import { api } from "../api";
import type { ChartSeriesData } from "../api";
import { BreakdownBarChart } from "../charts/BreakdownBarChart";
import { ChartPanel } from "../charts/ChartPanel";
import { ComparisonBadge } from "../charts/ChartContext";
import { TrendLineChart } from "../charts/TrendLineChart";
import "../charts/charts.css";
import { TimeRangeSelector } from "../components/TimeRangeSelector";
import { DashboardShell } from "./DashboardShell";

export function TransportManagerDashboard() {
  const [days, setDays] = useState(30);
  const [otaTrend, setOtaTrend] = useState<ChartSeriesData | null>(null);
  const [delayReasons, setDelayReasons] = useState<ChartSeriesData | null>(null);

  useEffect(() => {
    api.getOtaTrend(days).then(setOtaTrend);
    api.getDelayReasons(days).then(setDelayReasons);
  }, [days]);

  return (
    <DashboardShell
      persona="transport_manager"
      heading="Fleet Operations"
      description="Live SLA, cost, and safety signals for the routes and vendors you manage day to day."
      charts={
        <div className="charts-grid">
          <div className="charts-grid-wide chart-range-row">
            <TimeRangeSelector value={days} onChange={setDays} />
          </div>
          <ChartPanel title="On-Time Arrival Rate" subtitle="Daily OTA %, breach threshold 15 min">
            {otaTrend && (
              <>
                <TrendLineChart data={otaTrend} valueSuffix="%" />
                <ComparisonBadge comparison={otaTrend.comparison} suffix="%" />
              </>
            )}
          </ChartPanel>
          <ChartPanel title="Delay Reason Breakdown" subtitle="Completed trips by delay cause">
            {delayReasons && (
              <>
                <BreakdownBarChart data={delayReasons} />
                <ComparisonBadge comparison={delayReasons.comparison} />
              </>
            )}
          </ChartPanel>
        </div>
      }
    />
  );
}
