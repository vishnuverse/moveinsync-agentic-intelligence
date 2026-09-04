import { useEffect, useState } from "react";
import { api } from "../api";
import type { ActivityEntry, PersonaId } from "../api";
import "./AgentActivity.css";

const PERSONA_LABEL: Record<PersonaId, string> = {
  transport_manager: "Transport Manager",
  line_manager: "Line Manager",
  transport_head: "Transport Head",
};

function formatTime(ts: string): string {
  return new Date(ts).toLocaleString([], {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function AgentActivity() {
  const [entries, setEntries] = useState<ActivityEntry[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.getActivity().then((res) => {
      setEntries(res);
      setLoading(false);
    });
  }, []);

  return (
    <div className="agent-activity">
      <p className="agent-activity-intro">
        Autonomous runs across all personas — none of these were triggered by a person clicking
        anything. Each row fired on a schedule tick or a live data event.
      </p>
      {loading && <p className="notification-empty">Loading activity…</p>}
      {!loading && (
        <ul className="agent-activity-list">
          {entries.map((entry) => (
            <li key={entry.id} className="agent-activity-item">
              <span
                className={`agent-activity-trigger agent-activity-trigger-${entry.triggered_by}`}
              >
                {entry.triggered_by === "schedule" ? "⏱" : "⚡"}
              </span>
              <div className="agent-activity-body">
                <div className="agent-activity-meta">
                  <span className="badge badge-neutral">{PERSONA_LABEL[entry.persona]}</span>
                  <span className="agent-activity-source">
                    {entry.triggered_by === "schedule" ? "Scheduled run" : "Event-triggered"}
                  </span>
                  <span className="notification-item-time">{formatTime(entry.timestamp)}</span>
                </div>
                <p className="agent-activity-action">{entry.action}</p>
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
