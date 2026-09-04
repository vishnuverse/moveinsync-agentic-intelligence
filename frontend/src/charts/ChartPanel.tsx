import type { ReactNode } from "react";
import { ErrorState, LoadingState } from "../components/AsyncStatus";
import "./charts.css";

interface ChartPanelProps {
  title: string;
  subtitle?: string;
  wide?: boolean;
  children: ReactNode;
  /** Optional -- charts fetched independently of the metric grid (see the
   * three persona Dashboard components) can report their own loading/error
   * state so a hung or failed chart request shows something other than a
   * silent blank panel. Omit for charts fed synchronously (e.g. the vendor
   * scorecard's sparklines). */
  status?: "loading" | "ready" | "error";
  onRetry?: () => void;
}

export function ChartPanel({
  title,
  subtitle,
  wide = false,
  children,
  status = "ready",
  onRetry,
}: ChartPanelProps) {
  return (
    <div className={`chart-panel${wide ? " charts-grid-wide" : ""}`}>
      <div className="chart-panel-header">
        <div className="chart-panel-title">{title}</div>
        {subtitle && <div className="chart-panel-subtitle">{subtitle}</div>}
      </div>
      {status === "loading" && <LoadingState label="Loading chart…" />}
      {status === "error" && (
        <ErrorState label="Couldn't load this chart." onRetry={onRetry ?? (() => {})} />
      )}
      {status === "ready" && children}
    </div>
  );
}

export function ChartEmptyState() {
  return <p className="chart-empty-state">No data for this period.</p>;
}
