import { useEffect, useState } from "react";
import { api } from "../api";
import type { ChartSeriesData, VendorScorecardData } from "../api";
import { BreakdownBarChart } from "../charts/BreakdownBarChart";
import { ChartPanel } from "../charts/ChartPanel";
import { ComparisonBadge, ContributorsList } from "../charts/ChartContext";
import { StackedAreaChart } from "../charts/StackedAreaChart";
import { VendorScorecard } from "../charts/VendorScorecard";
import "../charts/charts.css";
import { TimeRangeSelector } from "../components/TimeRangeSelector";
import { DashboardShell } from "./DashboardShell";

export function TransportHeadDashboard() {
  const [days, setDays] = useState(30);
  const [billingDiscrepancy, setBillingDiscrepancy] = useState<ChartSeriesData | null>(null);
  const [emissionsByFuel, setEmissionsByFuel] = useState<ChartSeriesData | null>(null);
  const [vendorScorecard, setVendorScorecard] = useState<VendorScorecardData | null>(null);

  useEffect(() => {
    const months = Math.max(1, Math.round(days / 30));
    api.getBillingDiscrepancy(months).then(setBillingDiscrepancy);
    api.getEmissionsByFuel(days).then(setEmissionsByFuel);
    api.getVendorScorecard(days).then(setVendorScorecard);
  }, [days]);

  return (
    <DashboardShell
      persona="transport_head"
      heading="Strategic Overview"
      description="Fleet-wide cost, safety, vendor, and sustainability trends, benchmarked against industry targets."
      charts={
        <div className="charts-grid">
          <div className="charts-grid-wide chart-range-row">
            <TimeRangeSelector value={days} onChange={setDays} />
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
        </div>
      }
    />
  );
}
