import { useEffect, useState } from "react";
import { api } from "../api";
import type { ChartSeriesData, CostOptimizationResponse, VendorScorecardData } from "../api";
import { BreakdownBarChart } from "../charts/BreakdownBarChart";
import { ChartPanel } from "../charts/ChartPanel";
import { ComparisonBadge, ContributorsList } from "../charts/ChartContext";
import { StackedAreaChart } from "../charts/StackedAreaChart";
import { TrendLineChart } from "../charts/TrendLineChart";
import { VendorScorecard } from "../charts/VendorScorecard";
import "../charts/charts.css";
import { CostOptimizationPanel } from "../components/CostOptimizationPanel";
import { DateRangeSelector } from "../components/DateRangeSelector";
import { useDateRange } from "../hooks/useDateRange";
import { DashboardShell } from "./DashboardShell";

export function TransportHeadDashboard() {
  const { coverage, range, days, presetDays, bounds, setPresetDays, setCustomRange, ready } = useDateRange(30);
  const [billingDiscrepancy, setBillingDiscrepancy] = useState<ChartSeriesData | null>(null);
  const [emissionsByFuel, setEmissionsByFuel] = useState<ChartSeriesData | null>(null);
  const [vendorScorecard, setVendorScorecard] = useState<VendorScorecardData | null>(null);
  const [costOptimization, setCostOptimization] = useState<CostOptimizationResponse | null>(null);
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

  return (
    <DashboardShell
      persona="transport_head"
      heading="Strategic Overview"
      description="Fleet-wide cost, safety, vendor, and sustainability trends, benchmarked against industry targets."
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
          <ChartPanel title="Billing Discrepancy" subtitle="Overbilled distance × billed rate, per cycle, by vendor">
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
