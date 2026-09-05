import { useEffect, useState } from "react";
import { api } from "../api";
import type { ChartSeriesData, CostOptimizationResponse, TimelineDay, VendorScorecardData } from "../api";
import { BreakdownBarChart } from "../charts/BreakdownBarChart";
import { ChartPanel } from "../charts/ChartPanel";
import { ComparisonBadge, ContributorsList } from "../charts/ChartContext";
import { StackedAreaChart } from "../charts/StackedAreaChart";
import { TrendLineChart } from "../charts/TrendLineChart";
import { VendorScorecard } from "../charts/VendorScorecard";
import "../charts/charts.css";
import { CostOptimizationPanel } from "../components/CostOptimizationPanel";
import { DateRangeSelector } from "../components/DateRangeSelector";
import { SignalTimeline } from "../components/SignalTimeline";
import { useDateRange } from "../hooks/useDateRange";
import { DashboardShell } from "./DashboardShell";

export function TransportHeadDashboard() {
  const { coverage, range, days, presetDays, bounds, setPresetDays, setCustomRange, ready } = useDateRange(30);
  const [billingDiscrepancy, setBillingDiscrepancy] = useState<ChartSeriesData | null>(null);
  const [emissionsByFuel, setEmissionsByFuel] = useState<ChartSeriesData | null>(null);
  const [vendorScorecard, setVendorScorecard] = useState<VendorScorecardData | null>(null);
  const [costOptimization, setCostOptimization] = useState<CostOptimizationResponse | null>(null);
  const [timelineDays, setTimelineDays] = useState<TimelineDay[]>([]);
  // SP-B: LLM-filtering-gate visibility (plan §6) -- how many signals were
  // suppressed/rule-only/escalated, and today's LLM call volume vs budget.
  // Deliberately NOT driven by the date-range picker below: gate_decisions
  // rows carry real wall-clock timestamps (unaffected by the trip data's
  // live-replay-tail gap), so "last 14 days" here already means something.
  const [signalGateFunnel, setSignalGateFunnel] = useState<ChartSeriesData | null>(null);
  const [llmUsage, setLlmUsage] = useState<ChartSeriesData | null>(null);

  useEffect(() => {
    if (!ready || !range) return;
    api.getBillingDiscrepancy(undefined, range).then(setBillingDiscrepancy);
    api.getEmissionsByFuel(undefined, range).then(setEmissionsByFuel);
    api.getVendorScorecard(undefined, range).then(setVendorScorecard);
    api.getCostOptimization(range.since, range.until).then(setCostOptimization);
  }, [ready, range]);

  useEffect(() => {
    api.getSignalGateFunnel(30).then(setSignalGateFunnel);
    api.getLlmUsage(14).then(setLlmUsage);
  }, []);

  useEffect(() => {
    if (!coverage?.start_date || !coverage?.dense_end_date) return;
    api
      .getSignalTimeline("transport_head", undefined, { since: coverage.start_date, until: coverage.dense_end_date })
      .then((res) => setTimelineDays(res.days));
  }, [coverage]);

  return (
    <DashboardShell
      persona="transport_head"
      heading="Strategic Overview"
      description="Fleet-wide cost, safety, vendor, and sustainability trends, benchmarked against industry targets."
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
              title="Cost & Sustainability Activity"
              subtitle="Click, or click-drag, a range to inspect it — background color is daily spend; the ring marks a day whose average emissions exceeded the sustainability baseline."
              days={timelineDays}
              selectedSince={range.since}
              selectedUntil={range.until}
              onSelectRange={setCustomRange}
              formatPrimary={(n) => `₹${n.toLocaleString("en-IN")}`}
              primaryLegendLabel="Total spend"
              markerLegendLabel="Above-baseline emissions day"
              recommendedActions={(totalPrimary, totalMarkers) => {
                const actions: string[] = [];
                if (totalMarkers > 0) {
                  actions.push(
                    `${totalMarkers} day${totalMarkers === 1 ? "" : "s"} averaged above the sustainability baseline — prioritize those routes for EV/hybrid rotation.`,
                  );
                }
                if (totalPrimary > 0) {
                  actions.push(
                    "Cross-check the highest-spend days against the Vendor Scorecard below for the vendor(s) driving them.",
                  );
                } else {
                  actions.push("No spend recorded in this window.");
                }
                return actions;
              }}
            />
          )}
        </div>
      }
      charts={
        <div className="charts-grid">
          <ChartPanel title="Billing Discrepancy" subtitle="Slab-tier overbilling (billed vs. correct slab for actual distance), per cycle, by vendor">
            {billingDiscrepancy && (
              <>
                <BreakdownBarChart data={billingDiscrepancy} valuePrefix="₹" stacked />
                <ContributorsList
                  contributors={billingDiscrepancy.contributors}
                  title="Responsible for the gap"
                />
              </>
            )}
          </ChartPanel>
          <ChartPanel title="Emissions by Fuel Type" subtitle="Weekly CO2 (tonnes), Diesel / Petrol / Electric">
            {emissionsByFuel && (
              <>
                <StackedAreaChart data={emissionsByFuel} valueSuffix="t" />
                <ComparisonBadge comparison={emissionsByFuel.comparison} suffix=" g/pkm" />
              </>
            )}
          </ChartPanel>
          <ChartPanel title="Vendor Scorecard" subtitle="SLA %, cost/km, incidents, weekly SLA trend" wide>
            {vendorScorecard && <VendorScorecard data={vendorScorecard} />}
          </ChartPanel>
          <ChartPanel
            title="Cost Optimization"
            subtitle="Spend for the selected window vs. its own trailing average, with vendor billing-consistency opportunities"
            wide
          >
            <CostOptimizationPanel data={costOptimization} />
          </ChartPanel>
          <ChartPanel
            title="Signal Filtering Funnel"
            subtitle="How many alerts were suppressed, resolved by rule, or escalated to the LLM"
          >
            {signalGateFunnel && <BreakdownBarChart data={signalGateFunnel} stacked />}
          </ChartPanel>
          <ChartPanel title="LLM Call Volume" subtitle="Daily calls vs. budget (tune thresholds in Settings)">
            {llmUsage && <TrendLineChart data={llmUsage} />}
          </ChartPanel>
        </div>
      }
    />
  );
}
