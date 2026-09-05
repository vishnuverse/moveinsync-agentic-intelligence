import { useEffect, useRef, useState } from "react";
import type { ActivityTrigger, LiveEvent, LiveEventKind, PersonaId } from "./types";

// SP: a Server-Sent-Events hook for GET /api/sse/{persona}. Each `data:` frame
// carries the SAME JSON shape the existing WebSocket feed does (liveEvents.ts):
// the notification kinds notification | needs_intervention | dispatched |
// rejected, and -- per the API contract -- activity frames may also arrive.
//
// Like liveEvents.ts this is deliberately self-contained and DEFENSIVE: it is
// inert in mock mode or when there's no window/EventSource, it never throws,
// and it reconnects with backoff. It exposes an onFrame callback (kept in a
// ref so reconnects never fire a stale closure) rather than owning any list,
// so each consumer decides how to react to the frames it cares about.

// Mock is opt-in (matches api/index.ts): only "true" turns the mock client on,
// so by default (real backend) the stream is active.
const USE_MOCK = import.meta.env.VITE_USE_MOCK === "true";

const RECONNECT_BASE_MS = 1000;
const RECONNECT_MAX_MS = 15000;

/**
 * Derive the SSE base URL. Priority:
 *  1. VITE_SSE_BASE_URL explicit override (e.g. http://localhost:8000/api)
 *  2. VITE_API_BASE_URL when it's absolute http(s) (used verbatim)
 *  3. A relative VITE_API_BASE_URL (e.g. "/api", the docker/Caddy default)
 *     resolved against the page's own origin -> http(s)://<host>/api
 * Returns null when there's no window (SSR / tests) so callers stay inert.
 * EventSource speaks plain http(s), so -- unlike the WS hook -- no scheme swap.
 */
export function deriveSseBase(): string | null {
  const override = import.meta.env.VITE_SSE_BASE_URL as string | undefined;
  if (override && override.trim()) return override.replace(/\/+$/, "");

  const apiBase = (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? "/api";

  // Absolute http(s) base -> use as-is.
  if (/^https?:\/\//i.test(apiBase)) {
    return apiBase.replace(/\/+$/, "");
  }

  // Relative base -> resolve against the current origin.
  if (typeof window === "undefined" || !window.location) return null;
  const path = apiBase.startsWith("/") ? apiBase : `/${apiBase}`;
  return `${window.location.origin}${path.replace(/\/+$/, "")}`;
}

// A parsed SSE frame. The notification kinds share LiveEvent's shape; an
// activity frame additionally (or instead) carries an ActivityEntry-like body,
// so those fields are surfaced as optional here for consumers to sniff.
export interface LiveStreamFrame extends LiveEvent {
  id?: string | number;
  action?: string;
  timestamp?: string;
  triggered_by?: ActivityTrigger;
}

const NOTIFICATION_KINDS: ReadonlySet<string> = new Set<LiveEventKind>([
  "notification",
  "needs_intervention",
  "dispatched",
  "rejected",
]);

/** True for frames that concern a notification (any of the four WS kinds). */
export function isNotificationFrame(frame: LiveStreamFrame): boolean {
  return typeof frame.kind === "string" && NOTIFICATION_KINDS.has(frame.kind);
}

/** True for an activity frame: kind "activity", or an ActivityEntry-like body
 *  (a free-text `action`). Lets Agent Activity react without a fixed kind. */
export function isActivityFrame(frame: LiveStreamFrame): boolean {
  // `kind` is typed to the four notification kinds; an activity frame may carry
  // kind "activity" (widen to string to compare) or just a free-text `action`.
  return (frame.kind as string) === "activity" || typeof frame.action === "string";
}

export type LiveStreamState = "connecting" | "open" | "offline" | "disabled";

export interface UseLiveStreamOptions {
  onFrame?: (frame: LiveStreamFrame) => void;
}

export interface UseLiveStreamResult {
  connection: LiveStreamState;
  /** ISO timestamp of the last received frame, or null if none yet. */
  lastEventAt: string | null;
}

export function useLiveStream(
  persona: PersonaId | string,
  options?: UseLiveStreamOptions,
): UseLiveStreamResult {
  const [connection, setConnection] = useState<LiveStreamState>(
    USE_MOCK ? "disabled" : "connecting",
  );
  const [lastEventAt, setLastEventAt] = useState<string | null>(null);

  // Keep the latest onFrame in a ref so the effect below doesn't re-subscribe
  // (and the reconnect timer never calls a stale closure) when it changes.
  const onFrameRef = useRef(options?.onFrame);
  onFrameRef.current = options?.onFrame;

  const sourceRef = useRef<EventSource | null>(null);
  const reconnectRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const attemptRef = useRef(0);
  const activeRef = useRef(true);

  useEffect(() => {
    // Inert in mock mode -- no backend SSE endpoint to talk to.
    if (USE_MOCK) {
      setConnection("disabled");
      return;
    }
    if (typeof window === "undefined" || typeof EventSource === "undefined") {
      setConnection("offline");
      return;
    }

    const sseBase = deriveSseBase();
    if (!sseBase) {
      setConnection("offline");
      return;
    }

    activeRef.current = true;

    function scheduleReconnect() {
      if (!activeRef.current) return;
      const wait = Math.min(RECONNECT_BASE_MS * 2 ** attemptRef.current, RECONNECT_MAX_MS);
      attemptRef.current += 1;
      reconnectRef.current = setTimeout(connect, wait);
    }

    function connect() {
      if (!activeRef.current) return;
      setConnection((prev) => (prev === "open" ? prev : "connecting"));

      let source: EventSource;
      try {
        source = new EventSource(`${sseBase}/sse/${persona}`);
      } catch {
        setConnection("offline");
        scheduleReconnect();
        return;
      }
      sourceRef.current = source;

      source.onopen = () => {
        if (!activeRef.current) return;
        attemptRef.current = 0;
        setConnection("open");
      };

      source.onmessage = (ev) => {
        if (!activeRef.current) return;
        let frame: LiveStreamFrame | null = null;
        try {
          frame = JSON.parse(ev.data) as LiveStreamFrame;
        } catch {
          return; // ignore non-JSON / malformed frames, never crash
        }
        if (!frame || typeof frame.kind !== "string") return;
        setLastEventAt(frame.published_at ?? new Date().toISOString());
        try {
          onFrameRef.current?.(frame);
        } catch {
          // a consumer handler must never take the stream down
        }
      };

      source.onerror = () => {
        if (!activeRef.current) return;
        // EventSource retries on its own while it's still CONNECTING; only take
        // over with our own backoff once it has actually closed.
        setConnection((prev) => (prev === "open" ? "offline" : prev));
        if (source.readyState === EventSource.CLOSED) {
          try {
            source.close();
          } catch {
            // ignore
          }
          setConnection("offline");
          scheduleReconnect();
        }
      };
    }

    connect();

    return () => {
      activeRef.current = false;
      if (reconnectRef.current) clearTimeout(reconnectRef.current);
      const source = sourceRef.current;
      if (source) {
        source.onopen = null;
        source.onmessage = null;
        source.onerror = null;
        try {
          source.close();
        } catch {
          // ignore
        }
      }
      sourceRef.current = null;
      attemptRef.current = 0;
    };
  }, [persona]);

  return { connection, lastEventAt };
}
