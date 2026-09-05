import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api";
import type { ActivityEntry, ActivityTrigger, PersonaId } from "../api";
import { isActivityFrame, useLiveStream, type LiveStreamFrame } from "../api/liveStream";
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
// Light fallback refresh for the global feed. SSE is preferred and prepends
// live entries immediately; this only guards against activity frames whose
// exact kind/shape the backend may not emit (see task note + liveStream.ts).
const POLL_MS = 20000;
const MAX_LIVE_EXTRAS = 50;

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

// Build an ActivityEntry from a live SSE activity frame. Returns null when the
// frame carries no free-text action (i.e. it's not really an activity frame).
function frameToActivity(frame: LiveStreamFrame): ActivityEntry | null {
  if (typeof frame.action !== "string") return null;
  const id =
    frame.id != null
      ? String(frame.id)
      : frame.notification_id != null
        ? `live-${frame.notification_id}`
        : `live-${frame.published_at ?? Date.now()}`;
  const persona = (frame.persona as PersonaId) ?? "transport_manager";
  const triggered_by: ActivityTrigger = frame.triggered_by === "schedule" ? "schedule" : "event";
  return {
    id,
    persona,
    action: frame.action,
    timestamp: frame.timestamp ?? frame.published_at ?? new Date().toISOString(),
    triggered_by,
  };
}

export function AgentActivity() {
  const { persona, localActivity } = useAppState();
  const [entries, setEntries] = useState<ActivityEntry[]>([]);
  const [total, setTotal] = useState(0);
  const [status, setStatus] = useState<"loading" | "ready" | "error">("loading");
  const [loadingMore, setLoadingMore] = useState(false);
  // Live-pushed entries kept separate from the server-paginated list so a
  // fallback poll refreshing `entries` never drops a freshly streamed run.
  const [liveExtras, setLiveExtras] = useState<ActivityEntry[]>([]);

  const shownCountRef = useRef(0);
  shownCountRef.current = entries.length;

  const load = useCallback(
    (silent = false) => {
      if (!silent) setStatus("loading");
      const limit = Math.max(PAGE_SIZE, shownCountRef.current);
      withTimeout(api.getActivity({ limit, offset: 0 }))
        .then((res) => {
          setEntries(res.items);
          setTotal(res.total);
          setStatus("ready");
        })
        .catch(() => {
          if (!silent) setStatus("error");
        });
    },
    [],
  );

  useEffect(() => {
    load();
  }, [load]);

  const loadMore = useCallback(() => {
    setLoadingMore(true);
    withTimeout(api.getActivity({ limit: PAGE_SIZE, offset: shownCountRef.current }))
      .then((res) => {
        setEntries((prev) => {
          const seen = new Set(prev.map((e) => e.id));
          return [...prev, ...res.items.filter((e) => !seen.has(e.id))];
        });
        setTotal(res.total);
      })
      .catch(() => {
        /* keep loaded pages; button stays available */
      })
      .finally(() => setLoadingMore(false));
  }, []);

  // SSE: prefer live prepend. An activity frame with a body becomes a new
  // entry; an activity frame without a usable body triggers a silent refresh.
  useLiveStream(persona, {
    onFrame: (frame) => {
      if (!isActivityFrame(frame)) return;
      const entry = frameToActivity(frame);
      if (entry) {
        setLiveExtras((prev) => {
          if (prev.some((e) => e.id === entry.id)) return prev;
          return [entry, ...prev].slice(0, MAX_LIVE_EXTRAS);
        });
      } else {
        load(true);
      }
    },
  });

  // Fallback poll: only meaningful if SSE isn't delivering activity frames.
  useEffect(() => {
    const id = setInterval(() => load(true), POLL_MS);
    return () => clearInterval(id);
  }, [load]);

  // Resolved approve/reject decisions are recorded locally (see
  // AppStateContext) because the backend's pipeline_runs log only ever
  // records autonomous scheduler/event runs, never a human's resume
  // decision -- merging them in here is what makes that decision durable
  // instead of visible only for the moment the trace drawer was open.
  // liveExtras (SSE-pushed) and localActivity are unioned ahead of the
  // server page and de-duplicated by id so nothing shows twice.
  const merged = useMemo(() => {
    const seen = new Set<string>();
    const out: ActivityEntry[] = [];
    for (const e of [...localActivity, ...liveExtras, ...entries]) {
      if (seen.has(e.id)) continue;
      seen.add(e.id);
      out.push(e);
    }
    return out.sort((a, b) => (a.timestamp < b.timestamp ? 1 : -1));
  }, [entries, liveExtras, localActivity]);

  const hasMore = entries.length < total;
  const totalKnown = Math.max(total, merged.length);

  return (
    <div className="agent-activity">
      {status === "loading" && <LoadingState label="Loading activity…" />}
      {status === "error" && <ErrorState label="Couldn't load the activity feed." onRetry={() => load()} />}
      {status === "ready" && merged.length === 0 && (
        <EmptyState label="No autonomous runs recorded yet." />
      )}
      {status === "ready" && merged.length > 0 && (
        <>
          <p className="agent-activity-count">
            Showing {merged.length} of {totalKnown} run{totalKnown === 1 ? "" : "s"}
          </p>
          <ul className="agent-activity-list">
            {merged.map((entry) => {
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
          {hasMore && (
            <button
              type="button"
              className="btn btn-secondary agent-activity-more"
              onClick={loadMore}
              disabled={loadingMore}
            >
              {loadingMore ? "Loading…" : `Load more (${total - entries.length} left)`}
            </button>
          )}
        </>
      )}
    </div>
  );
}
