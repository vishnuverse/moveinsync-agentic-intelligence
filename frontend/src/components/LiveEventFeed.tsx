import { useEffect, useMemo, useState } from "react";
import { api } from "../api";
import type { DemoScenario, LiveEvent, LiveEventKind } from "../api";
import { useLiveEvents } from "../api/liveEvents";
import { useAppState } from "../state/AppStateContext";
import { IconAlert, IconBolt, IconClock } from "./icons";
import "./LiveEventFeed.css";

// --- Local, self-contained status -> color/icon/label map (SP-A A4). Kept
// LOCAL to this feature on purpose so the concurrent SP-C color-system work
// can't collide with it. NOTE: SP-C may later unify these into the shared
// token layer (frontend/src/theme/variables.css already has close analogues:
// --color-critical/-warning/-good/-info/-neutral) -- when that lands, this
// map should defer to those tokens instead of the Carbon-ish literals below.
type Tone = "critical" | "warning" | "info" | "success" | "neutral";

const TONE_STYLE: Record<Tone, { bg: string; fg: string }> = {
  critical: { bg: "#da1e28", fg: "#ffffff" },
  warning: { bg: "#f1c21b", fg: "#1b1b1b" }, // dark text on yellow (contrast)
  info: { bg: "#0043ce", fg: "#ffffff" },
  success: { bg: "#24a148", fg: "#ffffff" },
  neutral: { bg: "#6f6f6f", fg: "#ffffff" },
};

// Row severity: prefer the event's own severity, else derive from its kind.
function severityTone(event: LiveEvent): Tone {
  if (event.severity === "critical") return "critical";
  if (event.severity === "warning") return "warning";
  if (event.severity === "info") return "info";
  switch (event.kind) {
    case "needs_intervention":
      return "warning";
    case "dispatched":
      return "success";
    case "rejected":
      return "neutral";
    default:
      return "info";
  }
}

const SEVERITY_LABEL: Record<Tone, string> = {
  critical: "Critical",
  warning: "Warning",
  info: "Info",
  success: "Resolved",
  neutral: "Info",
};

function SeverityIcon({ tone }: { tone: Tone }) {
  if (tone === "critical" || tone === "warning") return <IconAlert width={13} height={13} />;
  if (tone === "success") return <IconBolt width={13} height={13} />;
  return <IconClock width={13} height={13} />;
}

// Autonomy badge (A4): act-vs-ask, derived from the event kind.
function autonomyBadge(kind: LiveEventKind): { tone: Tone; label: string } {
  switch (kind) {
    case "needs_intervention":
      return { tone: "warning", label: "Needs your approval" };
    case "notification":
    case "dispatched":
      return { tone: "success", label: "Auto-resolved by agent" };
    case "rejected":
      return { tone: "neutral", label: "Dismissed" };
    default:
      return { tone: "neutral", label: "Agent event" };
  }
}

const PERSONA_LABEL: Record<string, string> = {
  transport_manager: "Transport Manager",
  line_manager: "Line Manager",
  transport_head: "Transport Head",
};

const SCENARIOS: Array<{ id: DemoScenario; label: string }> = [
  { id: "delay_spike", label: "Delay spike" },
  { id: "escort_violation", label: "Escort violation" },
  { id: "billing_discrepancy", label: "Billing discrepancy" },
  { id: "emissions_over_target", label: "Emissions over target" },
];

type WindowFilter = "all" | "15m" | "1h";
const WINDOW_MS: Record<WindowFilter, number | null> = {
  all: null,
  "15m": 15 * 60 * 1000,
  "1h": 60 * 60 * 1000,
};

function relativeTime(iso: string | undefined, nowMs: number): string {
  if (!iso) return "just now";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "just now";
  const secs = Math.max(0, Math.round((nowMs - then) / 1000));
  if (secs < 5) return "just now";
  if (secs < 60) return `${secs}s ago`;
  const mins = Math.floor(secs / 60);
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.floor(hrs / 24)}d ago`;
}

function eventTitle(event: LiveEvent): string {
  if (event.title) return event.title;
  if (event.summary) return event.summary;
  if (event.recommendation) return event.recommendation;
  const at = event.action_type ? ` (${event.action_type})` : "";
  switch (event.kind) {
    case "needs_intervention":
      return `Action needs approval${at}`;
    case "dispatched":
      return `Action dispatched${at}`;
    case "rejected":
      return `Action dismissed${at}`;
    default:
      return `New notification${at}`;
  }
}

export function LiveEventFeed() {
  const { persona } = useAppState();
  const { events, connection, lastEventAt, clear } = useLiveEvents(persona);

  const [scenario, setScenario] = useState<DemoScenario>("delay_spike");
  const [replayState, setReplayState] = useState<"idle" | "injecting" | "done" | "error">("idle");
  const [replayNote, setReplayNote] = useState<string>("");
  const [windowFilter, setWindowFilter] = useState<WindowFilter>("all");

  // A 1s ticking clock so "updated Ns ago" and per-row relative times stay
  // live without a network round-trip. Cheap: one setState per second.
  const [nowMs, setNowMs] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNowMs(Date.now()), 1000);
    return () => clearInterval(id);
  }, []);

  const filtered = useMemo(() => {
    const span = WINDOW_MS[windowFilter];
    if (span === null) return events;
    const cutoff = nowMs - span;
    return events.filter((e) => {
      const t = e.published_at ? new Date(e.published_at).getTime() : nowMs;
      return Number.isNaN(t) ? true : t >= cutoff;
    });
  }, [events, windowFilter, nowMs]);

  async function handleSimulate() {
    setReplayState("injecting");
    setReplayNote("Injecting real trips… agent reacting…");
    try {
      const res = await api.replayDemo({ scenario, count: 3 });
      const n = res.injected_trip_ids.length;
      const acted = res.pipeline_summary.length;
      setReplayState("done");
      setReplayNote(
        n === 0
          ? "No matching real rows to replay (mock mode or empty dataset)."
          : `Injected ${n} trip${n === 1 ? "" : "s"}; pipeline produced ${acted} step${acted === 1 ? "" : "s"}. Watch the feed.`,
      );
    } catch (err) {
      setReplayState("error");
      setReplayNote(err instanceof Error ? err.message : "Replay failed.");
    }
  }

  const isMock = connection === "disabled";
  const heartbeatClass =
    connection === "open" ? "live-hb-online" : connection === "connecting" ? "live-hb-connecting" : "live-hb-offline";
  const heartbeatLabel =
    connection === "open"
      ? "LIVE"
      : connection === "connecting"
        ? "connecting…"
        : connection === "disabled"
          ? "mock mode"
          : "offline";

  return (
    <div className="live-feed">
      <div className="live-feed-header">
        <div className="live-heartbeat" aria-live="polite">
          <span className={`live-hb-dot ${heartbeatClass}`} aria-hidden="true" />
          <span className="live-hb-label">{heartbeatLabel}</span>
          {connection === "open" && (
            <span className="live-hb-updated">
              {lastEventAt ? `updated ${relativeTime(lastEventAt, nowMs)}` : "waiting for events…"}
            </span>
          )}
        </div>

        <div className="live-window-filter" role="group" aria-label="Time window">
          {(["all", "15m", "1h"] as WindowFilter[]).map((w) => (
            <button
              key={w}
              type="button"
              className={`live-window-btn ${windowFilter === w ? "live-window-btn-active" : ""}`}
              onClick={() => setWindowFilter(w)}
              aria-pressed={windowFilter === w}
            >
              {w === "all" ? "All" : w === "15m" ? "Last 15m" : "Last 1h"}
            </button>
          ))}
        </div>
      </div>

      <div className="live-simulate">
        <label className="live-simulate-label" htmlFor="live-scenario">
          Simulate live day
        </label>
        <select
          id="live-scenario"
          className="live-scenario-select"
          value={scenario}
          onChange={(e) => setScenario(e.target.value as DemoScenario)}
          disabled={replayState === "injecting"}
        >
          {SCENARIOS.map((s) => (
            <option key={s.id} value={s.id}>
              {s.label}
            </option>
          ))}
        </select>
        <button
          type="button"
          className="btn btn-primary live-simulate-btn"
          onClick={handleSimulate}
          disabled={replayState === "injecting"}
        >
          {replayState === "injecting" ? "Injecting…" : "Simulate live day"}
        </button>
        {replayNote && (
          <span
            className={`live-simulate-note ${replayState === "error" ? "live-simulate-note-error" : ""}`}
            role="status"
          >
            {replayNote}
          </span>
        )}
      </div>

      {isMock && (
        <p className="live-feed-hint">
          The live feed is inactive in mock mode. Run the app against the real backend
          (VITE_USE_MOCK=false) to watch the agent sense → reason → act in real time.
        </p>
      )}

      {!isMock && filtered.length === 0 && (
        <p className="live-feed-empty">
          {events.length === 0
            ? "No live events yet. Press “Simulate live day” to inject real trips and watch the agent react."
            : "No events in this time window."}
        </p>
      )}

      <ul className="live-feed-list">
        {filtered.map((event, idx) => {
          const tone = severityTone(event);
          const badge = autonomyBadge(event.kind);
          const style = TONE_STYLE[tone];
          const badgeStyle = TONE_STYLE[badge.tone];
          const key = `${event.notification_id ?? "x"}-${event.kind}-${event.published_at ?? idx}`;
          return (
            <li key={key} className="live-feed-item" style={{ borderLeftColor: style.bg }}>
              <div className="live-feed-item-top">
                <span
                  className="live-severity"
                  style={{ background: style.bg, color: style.fg }}
                >
                  <SeverityIcon tone={tone} />
                  {SEVERITY_LABEL[tone]}
                </span>
                <span
                  className="live-autonomy"
                  style={{ background: badgeStyle.bg, color: badgeStyle.fg }}
                >
                  {badge.label}
                </span>
                {event.persona && (
                  <span className="live-persona">
                    {PERSONA_LABEL[String(event.persona)] ?? event.persona}
                  </span>
                )}
                <span className="live-feed-time">{relativeTime(event.published_at, nowMs)}</span>
              </div>
              <p className="live-feed-item-title">{eventTitle(event)}</p>
            </li>
          );
        })}
      </ul>

      {events.length > 0 && (
        <button type="button" className="live-feed-clear" onClick={clear}>
          Clear feed
        </button>
      )}
    </div>
  );
}
