import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import type { NotificationItem, NotificationStatus } from "../api";
import { useAppState } from "../state/AppStateContext";
import { EmptyState, ErrorState, LoadingState } from "./AsyncStatus";
import { withTimeout } from "../lib/timeout";
import "./NotificationInbox.css";

const SEVERITY_BADGE: Record<NotificationItem["severity"], string> = {
  critical: "badge-critical",
  warning: "badge-warning",
  info: "badge-info",
};

const STATUS_LABEL: Record<NotificationStatus, string> = {
  open: "Open",
  acked: "Acknowledged",
  "needs-intervention": "Needs your sign-off",
};

const STATUS_BADGE: Record<NotificationStatus, string> = {
  "needs-intervention": "badge-critical",
  open: "badge-neutral",
  acked: "badge-good",
};

const STATUS_RANK: Record<NotificationStatus, number> = {
  "needs-intervention": 0,
  open: 1,
  acked: 2,
};

function formatTime(ts: string): string {
  return new Date(ts).toLocaleString([], {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function NotificationInbox() {
  const { persona, uiState, setSelectedNotification, openTrace, onResolved } = useAppState();
  const [items, setItems] = useState<NotificationItem[]>([]);
  const [status, setStatus] = useState<"loading" | "ready" | "error">("loading");

  const load = useCallback(() => {
    setStatus("loading");
    withTimeout(api.getNotifications(persona))
      .then((res) => {
        setItems(res);
        setStatus("ready");
      })
      .catch(() => setStatus("error"));
  }, [persona]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => onResolved(() => load()), [onResolved, load]);

  function handleOpen(item: NotificationItem) {
    setSelectedNotification(item.id);
    openTrace({
      threadId: item.thread_id,
      title: item.message.length > 60 ? `${item.message.slice(0, 57)}…` : item.message,
      actions: item.status === "needs-intervention" ? "approve-reject" : "none",
      actionTargetId: item.status === "needs-intervention" ? item.id : null,
    });
  }

  const sorted = [...items].sort((a, b) => STATUS_RANK[a.status] - STATUS_RANK[b.status]);

  return (
    <div className="notification-inbox">
      {status === "loading" && <LoadingState label="Loading notifications…" />}
      {status === "error" && (
        <ErrorState label="Couldn't load notifications." onRetry={load} />
      )}
      {status === "ready" && sorted.length === 0 && (
        <EmptyState label="No notifications for this persona right now." />
      )}
      {status === "ready" &&
        sorted.map((item) => (
          <button
            key={item.id}
            className={`notification-item notification-item-${item.status} ${
              uiState.selectedNotificationId === item.id ? "notification-item-selected" : ""
            }`}
            onClick={() => handleOpen(item)}
          >
            <div className="notification-item-top">
              <span
                className={`notification-item-dot notification-item-dot-${item.status}`}
                aria-hidden="true"
              />
              <span className={`badge ${SEVERITY_BADGE[item.severity]}`}>{item.severity}</span>
              <span className={`badge ${STATUS_BADGE[item.status]}`}>
                {STATUS_LABEL[item.status]}
              </span>
              <span className="notification-item-time">{formatTime(item.created_at)}</span>
            </div>
            <p className="notification-item-message">{item.message}</p>
          </button>
        ))}
    </div>
  );
}
