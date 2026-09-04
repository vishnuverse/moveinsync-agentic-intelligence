import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import type { TraceStep } from "../api";
import { useAppState } from "../state/AppStateContext";
import { withTimeout } from "../lib/timeout";
import { ErrorState, LoadingState } from "./AsyncStatus";
import { IconClose } from "./icons";
import "./TraceDrawer.css";

const STEP_LABELS: Record<TraceStep["step"], string> = {
  signal_detected: "Signal Detected",
  sql_generated: "SQL Generated",
  sql_executed: "SQL Executed",
  context_built: "Context Attached",
  decision: "Decision",
};

function formatTime(ts: string): string {
  return new Date(ts).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

export function TraceDrawer() {
  const { persona, uiState, closeTrace, notifyResolved, recordActivity } = useAppState();
  const { trace } = uiState;
  const [steps, setSteps] = useState<TraceStep[]>([]);
  const [status, setStatus] = useState<"loading" | "ready" | "error">("loading");
  const [submitting, setSubmitting] = useState<"approve" | "reject" | null>(null);
  const [resolved, setResolved] = useState(false);
  const [confirmingReject, setConfirmingReject] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const closeBtnRef = useRef<HTMLButtonElement>(null);
  const lastFocusedRef = useRef<HTMLElement | null>(null);
  const drawerRef = useRef<HTMLElement>(null);

  function loadTrace() {
    if (!trace.threadId) return;
    setStatus("loading");
    withTimeout(api.getTrace(trace.threadId))
      .then((result) => {
        setSteps(result);
        setStatus("ready");
      })
      .catch(() => setStatus("error"));
  }

  useEffect(() => {
    if (!trace.open || !trace.threadId) return;
    setResolved(false);
    setConfirmingReject(false);
    setActionError(null);
    loadTrace();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [trace.open, trace.threadId]);

  // Move focus into the drawer on open and back to whatever triggered it on
  // close -- previously nothing happened to focus at all, so a keyboard or
  // screen-reader user opening the drawer stayed anchored on the trigger
  // button behind it.
  useEffect(() => {
    if (trace.open) {
      lastFocusedRef.current = document.activeElement as HTMLElement | null;
      closeBtnRef.current?.focus();
    } else {
      lastFocusedRef.current?.focus();
    }
  }, [trace.open]);

  function handleKeyDown(e: React.KeyboardEvent<HTMLElement>) {
    if (e.key !== "Tab") return;
    const container = drawerRef.current;
    if (!container) return;
    const focusable = container.querySelectorAll<HTMLElement>(
      'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
    );
    if (focusable.length === 0) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (e.shiftKey && document.activeElement === first) {
      e.preventDefault();
      last.focus();
    } else if (!e.shiftKey && document.activeElement === last) {
      e.preventDefault();
      first.focus();
    }
  }

  async function handleDecision(decision: "approve" | "reject") {
    if (!trace.actionTargetId) return;
    setSubmitting(decision);
    setActionError(null);
    try {
      const res = await withTimeout(
        api.resumeNotification(trace.actionTargetId, { decision }),
      );
      setResolved(true);
      setConfirmingReject(false);
      notifyResolved(trace.actionTargetId, res.status);
      recordActivity({
        id: `local-${trace.actionTargetId}-${Date.now()}`,
        persona,
        action: `You ${decision === "approve" ? "approved" : "rejected"}: "${
          trace.title ?? "a notification"
        }". Status now ${res.status}.`,
        timestamp: new Date().toISOString(),
        triggered_by: "event",
      });
    } catch {
      setActionError(
        `Couldn't record your ${decision}. The item is still pending -- try again.`,
      );
    } finally {
      setSubmitting(null);
    }
  }

  return (
    <>
      <div
        className={`trace-drawer-scrim ${trace.open ? "trace-drawer-scrim-open" : ""}`}
        onClick={closeTrace}
      />
      <aside
        ref={drawerRef}
        className={`trace-drawer ${trace.open ? "trace-drawer-open" : ""}`}
        aria-hidden={!trace.open}
        role="dialog"
        aria-modal="true"
        aria-label={trace.title ?? "Agent Trace"}
        onKeyDown={handleKeyDown}
      >
        <header className="trace-drawer-header">
          <div>
            <p className="trace-drawer-eyebrow">How was this computed?</p>
            <h3>{trace.title ?? "Agent Trace"}</h3>
          </div>
          <button
            ref={closeBtnRef}
            className="trace-drawer-close"
            onClick={closeTrace}
            aria-label="Close trace"
          >
            <IconClose width={16} height={16} />
          </button>
        </header>

        <div className="trace-drawer-body">
          {status === "loading" && <LoadingState label="Loading trace…" />}
          {status === "error" && (
            <ErrorState label="Couldn't load this trace." onRetry={loadTrace} />
          )}
          {status === "ready" && steps.length === 0 && (
            <p className="trace-drawer-loading">No trace available for this thread yet.</p>
          )}
          {status === "ready" && steps.length > 0 && (
            <ol className="trace-steps">
              {steps.map((step, idx) => (
                <li key={idx} className="trace-step">
                  <div className="trace-step-marker">
                    <span className={`trace-step-dot trace-step-dot-${step.step}`} />
                    {idx < steps.length - 1 && <span className="trace-step-line" />}
                  </div>
                  <div className="trace-step-content">
                    <div className="trace-step-heading">
                      <span className="trace-step-label">{STEP_LABELS[step.step]}</span>
                      <span className="trace-step-time">{formatTime(step.timestamp)}</span>
                    </div>
                    <p className="trace-step-detail">{step.detail}</p>
                    {typeof step.retry_count === "number" && step.retry_count > 0 && (
                      <span className="badge badge-warning">
                        self-corrected × {step.retry_count}
                      </span>
                    )}
                    {step.sql && <pre className="trace-sql">{step.sql}</pre>}
                  </div>
                </li>
              ))}
            </ol>
          )}
        </div>

        {trace.actions === "approve-reject" && (
          <footer className="trace-drawer-footer">
            {resolved ? (
              <p className="trace-drawer-resolved">Decision recorded. Notification updated.</p>
            ) : confirmingReject ? (
              <div className="trace-drawer-confirm">
                <p className="trace-drawer-confirm-text">
                  Reject this? The item stays flagged and no automatic action is taken.
                </p>
                <div className="trace-drawer-confirm-actions">
                  <button
                    className="btn btn-secondary"
                    disabled={submitting !== null}
                    onClick={() => setConfirmingReject(false)}
                  >
                    Cancel
                  </button>
                  <button
                    className="btn btn-danger"
                    disabled={submitting !== null}
                    onClick={() => handleDecision("reject")}
                  >
                    {submitting === "reject" ? "Rejecting…" : "Confirm Reject"}
                  </button>
                </div>
              </div>
            ) : (
              <>
                <button
                  className="btn btn-primary"
                  disabled={submitting !== null}
                  onClick={() => handleDecision("approve")}
                >
                  {submitting === "approve" ? "Approving…" : "Approve"}
                </button>
                <button
                  className="trace-drawer-reject-trigger"
                  disabled={submitting !== null}
                  onClick={() => setConfirmingReject(true)}
                >
                  Reject…
                </button>
              </>
            )}
            {actionError && <p className="trace-drawer-error">{actionError}</p>}
          </footer>
        )}
      </aside>
    </>
  );
}
