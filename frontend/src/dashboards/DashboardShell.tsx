import { useEffect, useState, type ReactNode } from "react";
import { api } from "../api";
import type { MetricCardData, PersonaId } from "../api";
import { MetricCard } from "../components/MetricCard";
import { ReportsSection } from "../components/ReportsSection";
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

export function DashboardShell({ persona, heading, description, charts }: DashboardShellProps) {
  const [metrics, setMetrics] = useState<MetricCardData[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    api.getDashboard(persona).then((res) => {
      setMetrics(res);
      setLoading(false);
    });
  }, [persona]);

  return (
    <div>
      <div className="dashboard-heading">
        <h2>{heading}</h2>
        <p>{description}</p>
      </div>
      {loading && <p className="notification-empty">Loading dashboard…</p>}
      {!loading && (
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
