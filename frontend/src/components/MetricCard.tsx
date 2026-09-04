import type { MetricCardData } from "../api";
import { useAppState } from "../state/AppStateContext";
import { IconSearch, IconTrendDown, IconTrendFlat, IconTrendUp } from "./icons";
import "./MetricCard.css";

const TREND_ICON: Record<MetricCardData["trend"], typeof IconTrendUp> = {
  up: IconTrendUp,
  down: IconTrendDown,
  flat: IconTrendFlat,
};

const TREND_LABEL: Record<MetricCardData["trend"], string> = {
  up: "Trending up",
  down: "Trending down",
  flat: "Flat, no change",
};

const SEVERITY_LABEL: Record<MetricCardData["severity"], string> = {
  good: "On track",
  warning: "Watch",
  critical: "Needs attention",
  neutral: "Info",
};

export function MetricCard({ metric }: { metric: MetricCardData }) {
  const { openTrace } = useAppState();
  const TrendIcon = TREND_ICON[metric.trend];

  return (
    <div className="metric-card">
      <div className="metric-card-top">
        <span className="metric-card-label">
          <span
            className={`metric-card-dot metric-card-dot-${metric.severity}`}
            aria-hidden="true"
          />
          {metric.label}
        </span>
        <span className={`badge badge-${metric.severity}`}>{SEVERITY_LABEL[metric.severity]}</span>
      </div>
      <div className="metric-card-value-row">
        <span className="metric-card-value">{metric.value}</span>
        <span
          className={`metric-card-trend metric-card-trend-${metric.trend}`}
          aria-label={TREND_LABEL[metric.trend]}
        >
          <TrendIcon width={14} height={14} />
        </span>
      </div>
      <p className="metric-card-context">{metric.context_note}</p>
      <button
        className="link-btn metric-card-trace-btn"
        onClick={() =>
          openTrace({ threadId: metric.thread_id, title: metric.label, actions: "none" })
        }
      >
        <IconSearch width={13} height={13} /> How was this computed?
      </button>
    </div>
  );
}
