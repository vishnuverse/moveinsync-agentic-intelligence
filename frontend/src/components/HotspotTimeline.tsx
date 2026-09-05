import { useMemo, useState } from "react";
import type { HotspotDay } from "../api";
import "./HotspotTimeline.css";

interface HotspotTimelineProps {
  days: HotspotDay[];
  selectedSince: string;
  selectedUntil: string;
  onSelectRange: (since: string, until: string) => void;
}

// BUGFIX (found live: clicking a "red" cell showed "0 critical incidents,"
// reading as contradictory): the background color and the incident marker
// are deliberately two SEPARATE encodings, not one blended score. Escort
// violations run in the hundreds per day while critical/high incidents are
// genuinely rare (4 total across a 90-day window in the real dataset) --
// blending them into one weighted number meant escort volume alone always
// decided the color, so "red" never actually meant "an incident happened
// here," even though the color scale looked like it should. Now: background
// heat = escort-violation volume only (a real, separate risk signal in its
// own right -- "unsupervised exposure," not "confirmed event"); a distinct
// ring marker = at least one critical/high incident that day, regardless of
// how much escort-violation heat is also present. Clicking a plain red cell
// and seeing 0 incidents is now correct, not confusing: red there means
// "high unescorted-trip volume," and the marker (not the color) is what
// means "an incident happened."
function colorFor(escortCount: number, maxEscort: number): string {
  if (escortCount === 0) return "var(--color-surface-muted)";
  const t = maxEscort > 0 ? Math.min(escortCount / maxEscort, 1) : 0;
  // Amber -> red ramp, not a single flat "danger" color, so relative
  // volume across days is visible at a glance.
  if (t < 0.25) return "#fde68a";
  if (t < 0.5) return "#fbbf24";
  if (t < 0.75) return "#f97316";
  return "#dc2626";
}

function recommendedActions(totalEscort: number, totalCritical: number, totalHigh: number): string[] {
  const actions: string[] = [];
  if (totalCritical > 0) {
    actions.push(
      `${totalCritical} critical incident${totalCritical === 1 ? "" : "s"} in this window — confirm each has a completed driver/vendor debrief and that dashcam or GPS evidence is archived before closing.`,
    );
  }
  if (totalHigh > 0) {
    actions.push(
      `${totalHigh} high-severity incident${totalHigh === 1 ? "" : "s"} — review with the operating vendor and flag any driver appearing more than once.`,
    );
  }
  if (totalEscort > 0) {
    actions.push(
      `${totalEscort.toLocaleString()} unescorted late-night female trip${totalEscort === 1 ? "" : "s"} — enforce mandatory escort assignment at dispatch for LOGIN/LOGOUT legs in the 9pm–6am window, not just a post-hoc flag.`,
    );
    actions.push(
      "Escalate to the vendor(s) operating the routes above: escort non-compliance is a contract SLA term, not just an internal metric.",
    );
  }
  if (actions.length === 0) {
    actions.push("No Major Risk Hotspot events in this window — nothing needs action here.");
  }
  return actions;
}

export function HotspotTimeline({ days, selectedSince, selectedUntil, onSelectRange }: HotspotTimelineProps) {
  const [dragStart, setDragStart] = useState<string | null>(null);

  const maxEscort = useMemo(() => Math.max(1, ...days.map((d) => d.escort_violations)), [days]);

  const selected = useMemo(
    () => days.filter((d) => d.date >= selectedSince && d.date <= selectedUntil),
    [days, selectedSince, selectedUntil],
  );
  const totals = useMemo(
    () => ({
      escort: selected.reduce((s, d) => s + d.escort_violations, 0),
      critical: selected.reduce((s, d) => s + d.critical_incidents, 0),
      high: selected.reduce((s, d) => s + d.high_incidents, 0),
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
        <h3 className="hotspot-timeline-title">Major Risk Hotspots</h3>
        <p className="hotspot-timeline-subtitle">
          Click, or click-drag, a range to inspect it — background color is unescorted-trip volume; the ring marks a
          day with a critical/high incident.
        </p>
      </div>

      <div className="hotspot-timeline-strip" onMouseUp={() => setDragStart(null)} onMouseLeave={() => setDragStart(null)}>
        {days.map((d) => {
          const isSelected = d.date >= selectedSince && d.date <= selectedUntil;
          const hasIncident = d.critical_incidents > 0 || d.high_incidents > 0;
          return (
            <button
              key={d.date}
              type="button"
              className={`hotspot-timeline-cell${isSelected ? " hotspot-timeline-cell-selected" : ""}${
                hasIncident ? " hotspot-timeline-cell-incident" : ""
              }`}
              style={{ background: colorFor(d.escort_violations, maxEscort) }}
              title={`${d.date} — ${d.escort_violations.toLocaleString()} unescorted late-night trips (risk exposure)${
                hasIncident
                  ? ` · ${d.critical_incidents} critical + ${d.high_incidents} high incident${
                      d.critical_incidents + d.high_incidents === 1 ? "" : "s"
                    }`
                  : " · no critical/high incident this day"
              }`}
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
        <span className="hotspot-legend-swatch" style={{ background: "var(--color-surface-muted)" }} /> No unescorted trips
        <span className="hotspot-legend-swatch" style={{ background: "#fde68a" }} /> Low
        <span className="hotspot-legend-swatch" style={{ background: "#fbbf24" }} /> Moderate
        <span className="hotspot-legend-swatch" style={{ background: "#f97316" }} /> High
        <span className="hotspot-legend-swatch" style={{ background: "#dc2626" }} /> Severe
        <span className="hotspot-legend-swatch hotspot-legend-swatch-incident" /> Critical/high incident that day
      </div>

      <div className="hotspot-timeline-selection">
        <div className="hotspot-timeline-selection-stats">
          <span>
            {selectedSince} → {selectedUntil}
          </span>
          <span>{totals.escort.toLocaleString()} unescorted trips</span>
          <span>{totals.critical} critical incidents</span>
          <span>{totals.high} high-severity incidents</span>
        </div>
        <div className="hotspot-timeline-actions">
          <h4>Recommended next actions</h4>
          <ul>
            {recommendedActions(totals.escort, totals.critical, totals.high).map((action) => (
              <li key={action}>{action}</li>
            ))}
          </ul>
        </div>
      </div>
    </div>
  );
}
