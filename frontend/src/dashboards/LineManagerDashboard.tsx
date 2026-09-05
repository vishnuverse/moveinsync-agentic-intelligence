import { useEffect, useState } from "react";
import { api } from "../api";
import type { ChartSeriesData, PieChartData, TimelineDay } from "../api";
import { ChartPanel } from "../charts/ChartPanel";
import { ComparisonBadge } from "../charts/ChartContext";
import { DonutChart } from "../charts/DonutChart";
import { TrendLineChart } from "../charts/TrendLineChart";
import "../charts/charts.css";
import { DateRangeSelector } from "../components/DateRangeSelector";
import { SignalTimeline } from "../components/SignalTimeline";
import { useDateRange } from "../hooks/useDateRange";
import { DashboardShell } from "./DashboardShell";

export function LineManagerDashboard() {
  const { coverage, range, days, presetDays, bounds, setPresetDays, setCustomRange, ready } = useDateRange(30);
  const [noShowTrend, setNoShowTrend] = useState<ChartSeriesData | null>(null);
  const [absenceSplit, setAbsenceSplit] = useState<PieChartData | null>(null);
  const [timelineDays, setTimelineDays] = useState<TimelineDay[]>([]);

  useEffect(() => {
    if (!ready || !range) return;
    api.getNoShowTrend(undefined, range).then(setNoShowTrend);
    api.getAbsenceSplit(undefined, range).then(setAbsenceSplit);
  }, [ready, range]);

  // Full real history for the timeline backdrop, same pattern as the
  // Transport Manager hotspot timeline -- selecting a range on it drives
  // the same shared date-range state the picker and charts above use.
  useEffect(() => {
    if (!coverage?.start_date || !coverage?.dense_end_date) return;
    api
      .getSignalTimeline("line_manager", undefined, { since: coverage.start_date, until: coverage.dense_end_date })
      .then((res) => setTimelineDays(res.days));
  }, [coverage]);

  return (
    <DashboardShell
      persona="line_manager"
      heading="Team Commute Overview"
      description="How transport is affecting your team's attendance, safety, and cost — with context, not just numbers."
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
          {timelineDays.length > 0 && range && (
            <SignalTimeline
              title="Attendance Activity"
              subtitle="Click, or click-drag, a range to inspect it — background color is late-arrival volume; the ring marks a day where most lates were shuttle-caused."
              days={timelineDays}
              selectedSince={range.since}
              selectedUntil={range.until}
              onSelectRange={setCustomRange}
              formatPrimary={(n) => `${n.toLocaleString()} late marks`}
              primaryLegendLabel="Late marks"
              markerLegendLabel="Shuttle-caused lateness day"
              recommendedActions={(totalPrimary, totalMarkers) => {
                const actions: string[] = [];
                if (totalMarkers > 0) {
                  actions.push(
                    `${totalMarkers} day${totalMarkers === 1 ? "" : "s"} where most lateness was shuttle-caused — escalate the underlying delay pattern to the Transport Manager rather than counting it against employees.`,
                  );
                }
                if (totalPrimary > 0) {
                  actions.push(
                    "Review the individually-flagged employees below for lates that are NOT transport-correlated — those are the ones worth a direct conversation.",
                  );
                } else {
                  actions.push("No late marks in this window — nothing needs action here.");
                }
                return actions;
              }}
            />
          )}
        </div>
      }
      charts={
        <div className="charts-grid">
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
