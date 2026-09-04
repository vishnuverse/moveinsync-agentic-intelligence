import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "../api";
import type { ActivityEntry, PersonaId } from "../api";
import { useAppState } from "../state/AppStateContext";
import { withTimeout } from "../lib/timeout";
import { EmptyState, ErrorState, LoadingState } from "./AsyncStatus";
import { IconBolt, IconClock } from "./icons";
import "./AgentActivity.css";

const PERSONA_LABEL: Record<PersonaId, string> = {
  transport_manager: "Transport Manager",
  line_manager: "Line Manager",
  transport_head: "Transport Head",
};

const PAGE_SIZE = 25;

function formatTime(ts: string): string {
  return new Date(ts).toLocaleString([], {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

// Lightweight client-side classification of the backend's free-text `action`
// sentence -- no schema change needed. Matches activity_log.py's own fixed
// phrasing ("Couldn't finish reasoning...", "Checked in for..."), so a
// transient retry-pending entry and an empty scheduled check-in read
// visually distinct from genuine autonomous findings, instead of all three
// looking identical in the feed.
function activityKind(action: string): "retry" | "quiet" | "normal" {
  if (action.startsWith("Couldn't finish reasoning")) return "retry";
  if (action.startsWith("Checked in for")) return "quiet";
  return "normal";
}

export function AgentActivity() {
  const { localActivity } = useAppState();
  const [entries, setEntries] = useState<ActivityEntry[]>([]);
  const [status, setStatus] = useState<"loading" | "ready" | "error">("loading");
  const [visibleCount, setVisibleCount] = useState(PAGE_SIZE);

  const load = useCallback(() => {
    setStatus("loading");
    withTimeout(api.getActivity())
      .then((res) => {
        setEntries(res);
        setStatus("ready");
      })
      .catch(() => setStatus("error"));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  // Resolved approve/reject decisions are recorded locally (see
  // AppStateContext) because the backend's pipeline_runs log only ever
  // records autonomous scheduler/event runs, never a human's resume
  // decision -- merging them in here is what makes that decision durable
  // instead of visible only for the moment the trace drawer was open.
  const merged = useMemo(() => {
    const byId = new Set(entries.map((e) => e.id));
    const extra = localActivity.filter((e) => !byId.has(e.id));
    return [...extra, ...entries].sort((a, b) => (a.timestamp < b.timestamp ? 1 : -1));
  }, [entries, localActivity]);

  const visible = merged.slice(0, visibleCount);
  const remaining = merged.length - visible.length;

  return (
    <div className="agent-activity">
      {status === "loading" && <LoadingState label="Loading activity…" />}
      {status === "error" && <ErrorState label="Couldn't load the activity feed." onRetry={load} />}
      {status === "ready" && merged.length === 0 && (
        <EmptyState label="No autonomous runs recorded yet." />
      )}
      {status === "ready" && merged.length > 0 && (
        <>
          <p className="agent-activity-count">
            Showing {visible.length} of {merged.length} run{merged.length === 1 ? "" : "s"}
          </p>
          <ul className="agent-activity-list">
            {visible.map((entry) => {
              const kind = activityKind(entry.action);
              const TriggerIcon = entry.triggered_by === "schedule" ? IconClock : IconBolt;
              return (
                <li key={entry.id} className={`agent-activity-item agent-activity-item-${kind}`}>
                  <span
                    className={`agent-activity-trigger agent-activity-trigger-${entry.triggered_by}`}
                  >
                    <TriggerIcon width={14} height={14} />
                  </span>
                  <div className="agent-activity-body">
                    <div className="agent-activity-meta">
                      <span className="badge badge-neutral">{PERSONA_LABEL[entry.persona]}</span>
                      <span className="agent-activity-source">
                        {entry.triggered_by === "schedule" ? "Scheduled run" : "Event-triggered"}
                      </span>
                      <span className="notification-item-time">{formatTime(entry.timestamp)}</span>
                      {kind === "retry" && <span className="badge badge-warning">Retrying</span>}
                    </div>
                    <p className="agent-activity-action">{entry.action}</p>
                  </div>
                </li>
              );
            })}
          </ul>
          {remaining > 0 && (
            <button
              type="button"
              className="btn btn-secondary agent-activity-more"
              onClick={() => setVisibleCount((n) => n + PAGE_SIZE)}
            >
              Show {Math.min(remaining, PAGE_SIZE)} more
            </button>
          )}
        </>
      )}
    </div>
  );
}
