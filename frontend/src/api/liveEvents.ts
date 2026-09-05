import { useCallback, useEffect, useRef, useState } from "react";
import type { LiveEvent, PersonaId } from "./types";

// SP-A A1: a WebSocket hook for the `/api/ws/{persona}` live push channel
// (backend/app/api/ws.py forwards Redis `notifications:{persona}` frames).
//
// It is deliberately self-contained and DEFENSIVE: in mock mode, or if the
// socket can't open, it stays inert and never throws -- the rest of the app
// keeps working exactly as before. Nothing here touches shared state.

const MAX_EVENTS = 100;
// Reconnect backoff: 1s, 2s, 4s ... capped at 15s. Reset on a clean open.
const RECONNECT_BASE_MS = 1000;
const RECONNECT_MAX_MS = 15000;

const USE_MOCK = import.meta.env.VITE_USE_MOCK !== "false";

/**
 * Derive the WebSocket base URL. Priority:
 *  1. VITE_WS_BASE_URL explicit override (e.g. ws://localhost:8000/api)
 *  2. VITE_API_BASE_URL with http->ws / https->wss when it's absolute
 *     (e.g. http://localhost:8000/api -> ws://localhost:8000/api)
 *  3. A relative VITE_API_BASE_URL (e.g. "/api", the docker/Caddy default)
 *     resolved against the page's own origin -> ws(s)://<host>/api
 * Returns null when there's no window (SSR / tests) so callers stay inert.
 */
export function deriveWsBase(): string | null {
  const override = import.meta.env.VITE_WS_BASE_URL as string | undefined;
  if (override && override.trim()) return override.replace(/\/+$/, "");

  const apiBase = (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? "/api";

  // Absolute http(s) base -> swap the scheme.
  if (/^https?:\/\//i.test(apiBase)) {
    return apiBase.replace(/^http/i, "ws").replace(/\/+$/, "");
  }

  // Relative base -> resolve against the current origin.
  if (typeof window === "undefined" || !window.location) return null;
  const scheme = window.location.protocol === "https:" ? "wss" : "ws";
  const path = apiBase.startsWith("/") ? apiBase : `/${apiBase}`;
  return `${scheme}://${window.location.host}${path.replace(/\/+$/, "")}`;
}

export type LiveConnectionState = "connecting" | "open" | "offline" | "disabled";

export interface UseLiveEventsResult {
  events: LiveEvent[];
  connection: LiveConnectionState;
  /** ISO timestamp of the last received frame, or null if none yet. */
  lastEventAt: string | null;
  /** Clear the rolling buffer (does not affect the socket). */
  clear: () => void;
}

export function useLiveEvents(persona: PersonaId | string): UseLiveEventsResult {
  const [events, setEvents] = useState<LiveEvent[]>([]);
  const [connection, setConnection] = useState<LiveConnectionState>(
    USE_MOCK ? "disabled" : "connecting",
  );
  const [lastEventAt, setLastEventAt] = useState<string | null>(null);

  const socketRef = useRef<WebSocket | null>(null);
  const reconnectRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const attemptRef = useRef(0);
  // Guards async socket callbacks against firing after the effect tore down
  // (persona switch / unmount) -- prevents a stale socket from scheduling a
  // reconnect or flipping state for a persona we're no longer watching.
  const activeRef = useRef(true);

  const clear = useCallback(() => setEvents([]), []);

  useEffect(() => {
    // Inert in mock mode -- no backend WS to talk to.
    if (USE_MOCK) {
      setConnection("disabled");
      return;
    }

    const wsBase = deriveWsBase();
    if (!wsBase) {
      setConnection("offline");
      return;
    }

    activeRef.current = true;

    function scheduleReconnect() {
      if (!activeRef.current) return;
      const delay = Math.min(
        RECONNECT_BASE_MS * 2 ** attemptRef.current,
        RECONNECT_MAX_MS,
      );
      attemptRef.current += 1;
      reconnectRef.current = setTimeout(connect, delay);
    }

    function connect() {
      if (!activeRef.current) return;
      setConnection((prev) => (prev === "open" ? prev : "connecting"));

      let socket: WebSocket;
      try {
        socket = new WebSocket(`${wsBase}/ws/${persona}`);
      } catch {
        // Constructor can throw on a malformed URL -- treat as offline + retry.
        setConnection("offline");
        scheduleReconnect();
        return;
      }
      socketRef.current = socket;

      socket.onopen = () => {
        if (!activeRef.current) return;
        attemptRef.current = 0;
        setConnection("open");
      };

      socket.onmessage = (ev) => {
        if (!activeRef.current) return;
        let frame: LiveEvent | null = null;
        try {
          frame = JSON.parse(ev.data) as LiveEvent;
        } catch {
          return; // ignore non-JSON / malformed frames, never crash
        }
        if (!frame || typeof frame.kind !== "string") return;
        setLastEventAt(frame.published_at ?? new Date().toISOString());
        setEvents((prev) => [frame as LiveEvent, ...prev].slice(0, MAX_EVENTS));
      };

      socket.onerror = () => {
        // Let onclose drive the reconnect; just surface a degraded state.
        if (activeRef.current) setConnection((prev) => (prev === "open" ? prev : "offline"));
      };

      socket.onclose = () => {
        if (!activeRef.current) return;
        setConnection("offline");
        scheduleReconnect();
      };
    }

    connect();

    return () => {
      activeRef.current = false;
      if (reconnectRef.current) clearTimeout(reconnectRef.current);
      const socket = socketRef.current;
      if (socket) {
        // Detach handlers so a teardown-triggered close doesn't reconnect.
        socket.onopen = null;
        socket.onmessage = null;
        socket.onerror = null;
        socket.onclose = null;
        try {
          socket.close();
        } catch {
          // ignore
        }
      }
      socketRef.current = null;
      attemptRef.current = 0;
    };
  }, [persona]);

  return { events, connection, lastEventAt, clear };
}
