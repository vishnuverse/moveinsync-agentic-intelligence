import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api";
import type { NotificationItem, NotificationStatus } from "../api";
import { isNotificationFrame, useLiveStream } from "../api/liveStream";
import { useAppState } from "../state/AppStateContext";
import { EmptyState, ErrorState, LoadingState } from "./AsyncStatus";
import { withTimeout } from "../lib/timeout";
import "./NotificationInbox.css";

const PAGE_SIZE = 25;

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
  const [total, setTotal] = useState(0);
  const [status, setStatus] = useState<"loading" | "ready" | "error">("loading");
  const [loadingMore, setLoadingMore] = useState(false);

  // How many items are currently shown -- kept in a ref so a live/silent
  // refresh can re-fetch the same span without recreating `refresh` (which
  // would churn the onResolved subscription and the SSE handler).
  const shownCountRef = useRef(0);
  shownCountRef.current = items.length;

  // Load/refresh from offset 0. `silent` skips the loading flash so an
  // incoming live frame refreshes the list in place instead of blanking it.
  const refresh = useCallback(
    (silent = false) => {
      if (!silent) setStatus("loading");
      const limit = Math.max(PAGE_SIZE, shownCountRef.current);
      withTimeout(api.getNotifications(persona, { limit, offset: 0 }))
        .then((res) => {
          setItems(res.items);
          setTotal(res.total);
          setStatus("ready");
        })
        .catch(() => {
          if (!silent) setStatus("error");
        });
    },
    [persona],
  );

  useEffect(() => {
    refresh();
  }, [refresh]);

  useEffect(() => onResolved(() => refresh(true)), [onResolved, refresh]);

  // SSE: any notification-kind frame refreshes the list in place (defensive /
  // inert in mock mode -- see liveStream.ts).
  useLiveStream(persona, {
    onFrame: (frame) => {
      if (isNotificationFrame(frame)) refresh(true);
    },
  });

  const loadMore = useCallback(() => {
    setLoadingMore(true);
    withTimeout(api.getNotifications(persona, { limit: PAGE_SIZE, offset: shownCountRef.current }))
      .then((res) => {
        setItems((prev) => {
          const seen = new Set(prev.map((n) => n.id));
          return [...prev, ...res.items.filter((n) => !seen.has(n.id))];
        });
        setTotal(res.total);
      })
      .catch(() => {
        /* keep the pages already loaded; the button stays available to retry */
      })
      .finally(() => setLoadingMore(false));
  }, [persona]);

  function handleOpen(item: NotificationItem) {
    setSelectedNotification(item.id);
    openTrace({
      threadId: item.thread_id,
      title: item.message.length > 60 ? `${item.message.slice(0, 57)}…` : item.message,
      actions: item.status === "needs-intervention" ? "approve-reject" : "none",
      // SP-B §7: generalized from "only when pending sign-off" to "always" --
      // actionTargetId now means "the notification this trace concerns," not
      // just "the one you can approve/reject," so the false-positive action
      // is available on any item, not only ones awaiting sign-off.
      actionTargetId: item.id,
      isFalsePositive: item.is_false_positive,
    });
  }

  const sorted = [...items].sort((a, b) => STATUS_RANK[a.status] - STATUS_RANK[b.status]);
  const hasMore = items.length < total;

  return (
    <div className="notification-inbox">
      {status === "loading" && <LoadingState label="Loading notifications…" />}
      {status === "error" && (
        <ErrorState label="Couldn't load notifications." onRetry={() => refresh()} />
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
              {item.is_false_positive && <span className="badge badge-neutral">False positive</span>}
              <span className="notification-item-time">{formatTime(item.created_at)}</span>
            </div>
            <p className="notification-item-message">{item.message}</p>
          </button>
        ))}
      {status === "ready" && hasMore && (
        <button
          type="button"
          className="btn btn-secondary notification-inbox-more"
          onClick={loadMore}
          disabled={loadingMore}
        >
          {loadingMore ? "Loading…" : `Load more (${total - items.length} left)`}
        </button>
      )}
    </div>
  );
}
