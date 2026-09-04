import { useCallback, useEffect, useState, type ReactNode } from "react";
import { api } from "../api";
import type { MetricCardData, PersonaId } from "../api";
import { MetricCard } from "../components/MetricCard";
import { ReportsSection } from "../components/ReportsSection";
import { ErrorState, LoadingState } from "../components/AsyncStatus";
import { withTimeout } from "../lib/timeout";
import "./DashboardShell.css";

interface DashboardShellProps {
  persona: PersonaId;
  heading: string;
  description: string;
  /** Persona-specific Highcharts panels (frontend/src/charts/), rendered
   * below the metric cards -- charts add trend/breakdown visualization, they
   * don't replace the at-a-glance KPI cards above them. */
  charts?: ReactNode;
}

// Module-level, not component state -- DashboardPage swaps in a whole
// different Dashboard component per persona (TransportManagerDashboard vs
// LineManagerDashboard vs TransportHeadDashboard), so DashboardShell fully
// unmounts on every persona switch and would otherwise lose all loaded data
// and re-run the full spinner every single time, even flipping straight
// back to a persona already viewed this session.
const metricsCache = new Map<PersonaId, MetricCardData[]>();

export function DashboardShell({ persona, heading, description, charts }: DashboardShellProps) {
  const cached = metricsCache.get(persona);
  const [metrics, setMetrics] = useState<MetricCardData[]>(cached ?? []);
  const [status, setStatus] = useState<"loading" | "ready" | "error">(cached ? "ready" : "loading");

  const load = useCallback(() => {
    setStatus((prev) => (metricsCache.has(persona) ? prev : "loading"));
    withTimeout(api.getDashboard(persona))
      .then((res) => {
        metricsCache.set(persona, res);
        setMetrics(res);
        setStatus("ready");
      })
      .catch(() => {
        if (!metricsCache.has(persona)) setStatus("error");
      });
  }, [persona]);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <div>
      <div className="dashboard-heading">
        <h2>{heading}</h2>
        <p>{description}</p>
      </div>
      {status === "loading" && <LoadingState label="Loading dashboard…" />}
      {status === "error" && (
        <ErrorState label="Couldn't load metrics for this persona." onRetry={load} />
      )}
      {status === "ready" && (
        <div className="metric-grid">
          {metrics.map((metric) => (
            <MetricCard key={metric.id} metric={metric} />
          ))}
        </div>
      )}
      {charts}
      <ReportsSection persona={persona} />
    </div>
  );
}
