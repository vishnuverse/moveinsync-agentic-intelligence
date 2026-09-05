import { useMemo, useState } from "react";
import type { TimelineDay } from "../api";
import "./HotspotTimeline.css";

interface SignalTimelineProps {
  title: string;
  subtitle: string;
  days: TimelineDay[];
  selectedSince: string;
  selectedUntil: string;
  onSelectRange: (since: string, until: string) => void;
  /** How to format a day's primary_count in the tooltip/stats (e.g. "142
   * late marks" vs. "₹1,24,000"). */
  formatPrimary: (n: number) => string;
  primaryLegendLabel: string;
  markerLegendLabel: string;
  recommendedActions: (totalPrimary: number, totalMarkers: number) => string[];
}

function colorFor(value: number, max: number): string {
  if (value <= 0) return "var(--color-surface-muted)";
  const t = max > 0 ? Math.min(value / max, 1) : 0;
  if (t < 0.25) return "#fde68a";
  if (t < 0.5) return "#fbbf24";
  if (t < 0.75) return "#f97316";
  return "#dc2626";
}

/** Line Manager / Transport Head's analog of HotspotTimeline -- same
 * click/drag-to-select interaction and visual language (background heat +
 * a distinct marker ring, never blended into one score -- see
 * HotspotTimeline.tsx's colorFor() docstring for why that separation
 * matters), parameterized so each persona's own metric/labels/actions
 * plug in without duplicating the interaction logic three times. */
export function SignalTimeline({
  title,
  subtitle,
  days,
  selectedSince,
  selectedUntil,
  onSelectRange,
  formatPrimary,
  primaryLegendLabel,
  markerLegendLabel,
  recommendedActions,
}: SignalTimelineProps) {
  const [dragStart, setDragStart] = useState<string | null>(null);

  const maxPrimary = useMemo(() => Math.max(1, ...days.map((d) => d.primary_count)), [days]);

  const selected = useMemo(
    () => days.filter((d) => d.date >= selectedSince && d.date <= selectedUntil),
    [days, selectedSince, selectedUntil],
  );
  const totals = useMemo(
    () => ({
      primary: selected.reduce((s, d) => s + d.primary_count, 0),
      markers: selected.reduce((s, d) => s + d.marker_count, 0),
    }),
    [selected],
  );

  function handleMouseDown(date: string) {
    setDragStart(date);
    onSelectRange(date, date);
  }

  function handleMouseEnter(date: string) {
    if (!dragStart) return;
    const [a, b] = dragStart <= date ? [dragStart, date] : [date, dragStart];
    onSelectRange(a, b);
  }

  if (days.length === 0) return null;

  return (
    <div className="hotspot-timeline">
      <div className="hotspot-timeline-header">
        <h3 className="hotspot-timeline-title">{title}</h3>
        <p className="hotspot-timeline-subtitle">{subtitle}</p>
      </div>

      <div className="hotspot-timeline-strip" onMouseUp={() => setDragStart(null)} onMouseLeave={() => setDragStart(null)}>
        {days.map((d) => {
          const isSelected = d.date >= selectedSince && d.date <= selectedUntil;
          const hasMarker = d.marker_count > 0;
          return (
            <button
              key={d.date}
              type="button"
              className={`hotspot-timeline-cell${isSelected ? " hotspot-timeline-cell-selected" : ""}${
                hasMarker ? " hotspot-timeline-cell-incident" : ""
              }`}
              style={{ background: colorFor(d.primary_count, maxPrimary) }}
              title={`${d.date}: ${formatPrimary(d.primary_count)}${hasMarker ? ` — ${markerLegendLabel}` : ""}`}
              onMouseDown={() => handleMouseDown(d.date)}
              onMouseEnter={() => handleMouseEnter(d.date)}
            />
          );
        })}
      </div>
      <div className="hotspot-timeline-axis">
        <span>{days[0].date}</span>
        <span>{days[days.length - 1].date}</span>
      </div>

      <div className="hotspot-timeline-legend">
        <span className="hotspot-legend-swatch" style={{ background: "var(--color-surface-muted)" }} /> None
        <span className="hotspot-legend-swatch" style={{ background: "#fde68a" }} /> Low
        <span className="hotspot-legend-swatch" style={{ background: "#fbbf24" }} /> Moderate
        <span className="hotspot-legend-swatch" style={{ background: "#f97316" }} /> High
        <span className="hotspot-legend-swatch" style={{ background: "#dc2626" }} /> Severe
        <span className="hotspot-legend-swatch hotspot-legend-swatch-incident" /> {markerLegendLabel}
      </div>

      <div className="hotspot-timeline-selection">
        <div className="hotspot-timeline-selection-stats">
          <span>
            {selectedSince} → {selectedUntil}
          </span>
          <span>
            {primaryLegendLabel}: {formatPrimary(totals.primary)}
          </span>
          <span>
            {markerLegendLabel}: {totals.markers}
          </span>
        </div>
        <div className="hotspot-timeline-actions">
          <h4>Recommended next actions</h4>
          <ul>
            {recommendedActions(totals.primary, totals.markers).map((action) => (
              <li key={action}>{action}</li>
            ))}
          </ul>
        </div>
      </div>
    </div>
  );
}
