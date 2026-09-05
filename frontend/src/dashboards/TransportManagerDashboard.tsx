import { useEffect, useState } from "react";
import { api } from "../api";
import type { ChartSeriesData, HotspotDay } from "../api";
import { BreakdownBarChart } from "../charts/BreakdownBarChart";
import { ChartPanel } from "../charts/ChartPanel";
import { ComparisonBadge } from "../charts/ChartContext";
import { TrendLineChart } from "../charts/TrendLineChart";
import "../charts/charts.css";
import { DateRangeSelector } from "../components/DateRangeSelector";
import { HotspotTimeline } from "../components/HotspotTimeline";
import { useDateRange } from "../hooks/useDateRange";
import { DashboardShell } from "./DashboardShell";

export function TransportManagerDashboard() {
  const { coverage, range, days, presetDays, bounds, setPresetDays, setCustomRange, ready } = useDateRange(30);
  const [otaTrend, setOtaTrend] = useState<ChartSeriesData | null>(null);
  const [delayReasons, setDelayReasons] = useState<ChartSeriesData | null>(null);
  const [hotspotDays, setHotspotDays] = useState<HotspotDay[]>([]);

  useEffect(() => {
    if (!ready || !range) return;
    api.getOtaTrend(undefined, range).then(setOtaTrend);
    api.getDelayReasons(undefined, range).then(setDelayReasons);
  }, [ready, range]);

  // The hotspot timeline shows the org's FULL real history (not just the
  // dashboard's current selected range) so there's something to click/drag
  // across -- selecting a range on it drives the same shared date-range
  // state as the picker above, so every chart on this page updates too.
  useEffect(() => {
    if (!coverage?.start_date || !coverage?.dense_end_date) return;
    api
      .getHotspotTimeline(undefined, { since: coverage.start_date, until: coverage.dense_end_date })
      .then((res) => setHotspotDays(res.days));
  }, [coverage]);

  return (
    <DashboardShell
      persona="transport_manager"
      heading="Fleet Operations"
      description="Live SLA, cost, and safety signals for the routes and vendors you manage day to day."
      topContent={
        <div className="dashboard-top-timeline">
          <DateRangeSelector
            coverage={coverage}
            range={range}
            days={days}
            presetDays={presetDays}
            bounds={bounds}
            onPresetDays={setPresetDays}
            onCustomRange={setCustomRange}
          />
          {hotspotDays.length > 0 && range && (
            <HotspotTimeline
              days={hotspotDays}
              selectedSince={range.since}
              selectedUntil={range.until}
              onSelectRange={setCustomRange}
            />
          )}
        </div>
      }
      charts={
        <div className="charts-grid">
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
