import type { ReactNode } from "react";
import "./charts.css";

interface ChartPanelProps {
  title: string;
  subtitle?: string;
  wide?: boolean;
  children: ReactNode;
}

export function ChartPanel({ title, subtitle, wide = false, children }: ChartPanelProps) {
  return (
    <div className={`chart-panel${wide ? " charts-grid-wide" : ""}`}>
      <div className="chart-panel-header">
        <div className="chart-panel-title">{title}</div>
        {subtitle && <div className="chart-panel-subtitle">{subtitle}</div>}
      </div>
      {children}
    </div>
  );
}
