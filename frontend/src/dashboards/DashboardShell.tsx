import { useCallback, useEffect, useState, type ReactNode } from "react";
import { api } from "../api";
import type { MetricCardData, PersonaId } from "../api";
import { isNotificationFrame, useLiveStream } from "../api/liveStream";
import { MetricCard } from "../components/MetricCard";
import { ReportsSection } from "../components/ReportsSection";
import { ErrorState, LoadingState } from "../components/AsyncStatus";
import { withTimeout } from "../lib/timeout";
import "./DashboardShell.css";

interface DashboardShellProps {
  persona: PersonaId;
  heading: string;
  description: string;
  /** The date-range picker + hotspot/signal timeline, rendered ABOVE the
   * metric cards -- "the timeline at the top" is the entry point into the
   * page, not an afterthought below the charts it also drives. */
  topContent?: ReactNode;
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

export function DashboardShell({ persona, heading, description, topContent, charts }: DashboardShellProps) {
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

  // "Showcase what's happening" -- the metric-card grid is this page's
  // "right now" surface (it's built from the most recent notifications,
  // not the historical range the timeline/charts explore -- see
  // dashboard_cards.py's own docstring), so it's what re-renders live over
  // the SSE stream this app already runs. A new notification for this
  // persona re-fetches the cards and the badge below reflects real
  // connection state -- never a fabricated "streaming" claim.
  const { connection } = useLiveStream(persona, {
    onFrame: (frame) => {
      if (isNotificationFrame(frame)) load();
    },
  });

  return (
    <div>
      <div className="dashboard-heading">
        <h2>{heading}</h2>
        <p>{description}</p>
      </div>
      {topContent}
      <div className="dashboard-metrics-header">
        <h3 className="dashboard-metrics-title">Right Now</h3>
        <span className={`dashboard-live-badge dashboard-live-badge-${connection}`}>
          <span className="dashboard-live-dot" aria-hidden="true" />
          {connection === "open" ? "Live" : connection === "connecting" ? "Connecting…" : "Offline"}
        </span>
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
