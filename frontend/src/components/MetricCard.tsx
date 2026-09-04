import type { MetricCardData } from "../api";
import { useAppState } from "../state/AppStateContext";
import "./MetricCard.css";

const TREND_ICON: Record<MetricCardData["trend"], string> = {
  up: "▲",
  down: "▼",
  flat: "▬",
};

const SEVERITY_LABEL: Record<MetricCardData["severity"], string> = {
  good: "On track",
  warning: "Watch",
  critical: "Needs attention",
  neutral: "Info",
};

export function MetricCard({ metric }: { metric: MetricCardData }) {
  const { openTrace } = useAppState();

  return (
    <div className={`metric-card metric-card-${metric.severity}`}>
      <div className="metric-card-top">
        <span className="metric-card-label">{metric.label}</span>
        <span className={`badge badge-${metric.severity}`}>{SEVERITY_LABEL[metric.severity]}</span>
      </div>
      <div className="metric-card-value-row">
        <span className="metric-card-value">{metric.value}</span>
        <span className={`metric-card-trend metric-card-trend-${metric.trend}`}>
          {TREND_ICON[metric.trend]}
        </span>
      </div>
      <p className="metric-card-context">{metric.context_note}</p>
      <button
        className="link-btn metric-card-trace-btn"
        onClick={() =>
          openTrace({ threadId: metric.thread_id, title: metric.label, actions: "none" })
        }
      >
        🔍 How was this computed?
      </button>
    </div>
  );
}
