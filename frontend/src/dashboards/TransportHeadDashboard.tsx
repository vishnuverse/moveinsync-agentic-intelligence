import { useEffect, useState } from "react";
import { api } from "../api";
import type { ChartSeriesData, VendorScorecardData } from "../api";
import { BreakdownBarChart } from "../charts/BreakdownBarChart";
import { ChartPanel } from "../charts/ChartPanel";
import { StackedAreaChart } from "../charts/StackedAreaChart";
import { VendorScorecard } from "../charts/VendorScorecard";
import "../charts/charts.css";
import { DashboardShell } from "./DashboardShell";

export function TransportHeadDashboard() {
  const [billingDiscrepancy, setBillingDiscrepancy] = useState<ChartSeriesData | null>(null);
  const [emissionsByFuel, setEmissionsByFuel] = useState<ChartSeriesData | null>(null);
  const [vendorScorecard, setVendorScorecard] = useState<VendorScorecardData | null>(null);

  useEffect(() => {
    api.getBillingDiscrepancy().then(setBillingDiscrepancy);
    api.getEmissionsByFuel().then(setEmissionsByFuel);
    api.getVendorScorecard().then(setVendorScorecard);
  }, []);

  return (
    <DashboardShell
      persona="transport_head"
      heading="Strategic Overview"
      description="Fleet-wide cost, safety, vendor, and sustainability trends, benchmarked against industry targets."
      charts={
        <div className="charts-grid">
          <ChartPanel title="Billing Discrepancy" subtitle="Overbilled distance × billed rate, per cycle">
            {billingDiscrepancy && <BreakdownBarChart data={billingDiscrepancy} valuePrefix="₹" />}
          </ChartPanel>
          <ChartPanel title="Emissions by Fuel Type" subtitle="Weekly CO2 (tonnes), Diesel / Petrol / Electric">
            {emissionsByFuel && <StackedAreaChart data={emissionsByFuel} valueSuffix="t" />}
          </ChartPanel>
          <ChartPanel title="Vendor Scorecard" subtitle="SLA %, cost/km, incidents, weekly SLA trend" wide>
            {vendorScorecard && <VendorScorecard data={vendorScorecard} />}
          </ChartPanel>
        </div>
      }
    />
  );
}
