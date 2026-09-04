import { useEffect, useState } from "react";
import { api } from "../api";
import type { MetricCardData, PersonaId } from "../api";
import { MetricCard } from "../components/MetricCard";
import { ReportsSection } from "../components/ReportsSection";
import "./DashboardShell.css";

interface DashboardShellProps {
  persona: PersonaId;
  heading: string;
  description: string;
}

export function DashboardShell({ persona, heading, description }: DashboardShellProps) {
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
      <ReportsSection persona={persona} />
    </div>
  );
}
