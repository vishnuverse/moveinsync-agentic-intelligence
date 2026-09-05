import { useEffect, useState } from "react";
import { api } from "../api";
import type { DataCoverage } from "../api";
import { withTimeout } from "../lib/timeout";
import { LiveEventFeed } from "../components/LiveEventFeed";

function coverageLabel(c: DataCoverage): string {
  const trips = c.trip_count.toLocaleString();
  if (!c.start_date || !c.end_date) {
    return `Data window: no trips loaded yet (${trips} trips)`;
  }
  return `Data window: ${c.start_date} → ${c.end_date} (${trips} trips)`;
}

export function LivePage() {
  const [coverage, setCoverage] = useState<DataCoverage | null>(null);

  useEffect(() => {
    let active = true;
    withTimeout(api.getDataCoverage())
      .then((res) => {
        if (active) setCoverage(res);
      })
      .catch(() => {
        // Non-critical chrome -- if it fails we simply omit the label.
      });
    return () => {
      active = false;
    };
  }, []);

  return (
    <div>
      <div className="dashboard-heading">
        <div className="live-page-title-row">
          <h2>Live</h2>
          {coverage && <span className="live-data-window">{coverageLabel(coverage)}</span>}
        </div>
        <p>
          The agent, watched in real time. Events stream in as it senses new data, reasons about
          it, and either acts autonomously or asks for your sign-off. Press “Simulate live day” to
          replay real historical trips at demo pace and watch the pipeline react.
        </p>
      </div>
      <LiveEventFeed />
    </div>
  );
}
