import { useEffect, useState } from "react";
import { api } from "../api";
import type { ChartSeriesData } from "../api";
import { BreakdownBarChart } from "../charts/BreakdownBarChart";
import { ChartPanel } from "../charts/ChartPanel";
import { ComparisonBadge } from "../charts/ChartContext";
import { TrendLineChart } from "../charts/TrendLineChart";
import "../charts/charts.css";
import { DateRangeSelector } from "../components/DateRangeSelector";
import { useDateRange } from "../hooks/useDateRange";
import { DashboardShell } from "./DashboardShell";

export function TransportManagerDashboard() {
  const { coverage, range, days, presetDays, bounds, setPresetDays, setCustomRange, ready } = useDateRange(30);
  const [otaTrend, setOtaTrend] = useState<ChartSeriesData | null>(null);
  const [delayReasons, setDelayReasons] = useState<ChartSeriesData | null>(null);

  useEffect(() => {
    if (!ready || !range) return;
    api.getOtaTrend(undefined, range).then(setOtaTrend);
    api.getDelayReasons(undefined, range).then(setDelayReasons);
  }, [ready, range]);

  return (
    <DashboardShell
      persona="transport_manager"
      heading="Fleet Operations"
      description="Live SLA, cost, and safety signals for the routes and vendors you manage day to day."
      charts={
        <div className="charts-grid">
          <div className="charts-grid-wide chart-range-row">
            <DateRangeSelector
              coverage={coverage}
              range={range}
              days={days}
              presetDays={presetDays}
              bounds={bounds}
              onPresetDays={setPresetDays}
              onCustomRange={setCustomRange}
            />
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
