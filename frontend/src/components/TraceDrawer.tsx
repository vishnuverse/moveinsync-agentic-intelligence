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
  // SP-B (plan §8): the gate's own suppress/rule-only/escalate decision --
  // a distinct step type so "a rule decided this" reads visually different
  // from "the LLM decided this" (see .trace-step-dot-gate_decision in
  // TraceDrawer.css).
  gate_decision: "Gate Evaluated",
  sql_generated: "SQL Generated",
  sql_executed: "SQL Executed",
  context_built: "Context Attached",
  decision: "Decision",
  // BUGFIX: an escalated notification's thread_id is synthetic (created
  // directly by check_escalations, never passed through top_graph.invoke())
  // -- there's no checkpoint history under it, so its trace is the ORIGINAL
  // notification's trace with this one extra step appended, explaining the
  // promotion rather than 404ing on "How was this computed?".
  escalation: "Escalated",
};

function formatTime(ts: string): string {
  return new Date(ts).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

export function TraceDrawer() {
  const { persona, uiState, closeTrace, notifyResolved, recordActivity } = useAppState();
  const { trace } = uiState;
  const [steps, setSteps] = useState<TraceStep[]>([]);
  // SP-B (plan §8): "thoughts and query in a collapsed window" -- every step
  // starts collapsed; indices in this set are expanded. Reset whenever a new
  // trace loads (see loadTrace below).
  const [expandedSteps, setExpandedSteps] = useState<Set<number>>(new Set());
  const [status, setStatus] = useState<"loading" | "ready" | "error">("loading");
  const [submitting, setSubmitting] = useState<"approve" | "reject" | null>(null);
  const [resolved, setResolved] = useState(false);
  const [markedFalsePositive, setMarkedFalsePositive] = useState(false);
  const [confirmingReject, setConfirmingReject] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const closeBtnRef = useRef<HTMLButtonElement>(null);
  const lastFocusedRef = useRef<HTMLElement | null>(null);
  const drawerRef = useRef<HTMLElement>(null);

  function loadTrace() {
    if (!trace.threadId) return;
    setStatus("loading");
    setExpandedSteps(new Set());
    withTimeout(api.getTrace(trace.threadId))
      .then((result) => {
        setSteps(result);
        setStatus("ready");
      })
      .catch(() => setStatus("error"));
  }

  function toggleStep(idx: number) {
    setExpandedSteps((prev) => {
      const next = new Set(prev);
      if (next.has(idx)) next.delete(idx);
      else next.add(idx);
      return next;
    });
  }

  useEffect(() => {
    if (!trace.open || !trace.threadId) return;
    setResolved(false);
    setMarkedFalsePositive(false);
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

  async function handleMarkFalsePositive() {
    if (!trace.actionTargetId) return;
    setSubmitting("approve"); // reuse the existing busy-state styling, no separate "fp" submitting value needed
    setActionError(null);
    try {
      await withTimeout(api.markFalsePositive(trace.actionTargetId));
      setMarkedFalsePositive(true);
      notifyResolved(trace.actionTargetId, "acked");
      recordActivity({
        id: `local-fp-${trace.actionTargetId}-${Date.now()}`,
        persona,
        action: `You marked "${trace.title ?? "a notification"}" as a false positive.`,
        timestamp: new Date().toISOString(),
        triggered_by: "event",
      });
    } catch {
      setActionError("Couldn't record that as a false positive -- try again.");
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
              {steps.map((step, idx) => {
                const expanded = expandedSteps.has(idx);
                return (
                  <li key={idx} className="trace-step">
                    <div className="trace-step-marker">
                      <span className={`trace-step-dot trace-step-dot-${step.step}`} />
                      {idx < steps.length - 1 && <span className="trace-step-line" />}
                    </div>
                    <div className="trace-step-content">
                      <button
                        type="button"
                        className="trace-step-header"
                        aria-expanded={expanded}
                        onClick={() => toggleStep(idx)}
                      >
                        <span className="trace-step-label">{STEP_LABELS[step.step]}</span>
                        <span className="trace-step-time">{formatTime(step.timestamp)}</span>
                        <span className={`trace-step-chevron${expanded ? " trace-step-chevron-open" : ""}`} aria-hidden="true">
                          ▸
                        </span>
                      </button>
                      {expanded && (
                        <div className="trace-step-body">
                          <p className="trace-step-detail">{step.detail}</p>
                          {typeof step.retry_count === "number" && step.retry_count > 0 && (
                            <span className="badge badge-warning">
                              self-corrected × {step.retry_count}
                            </span>
                          )}
                          {step.recommendation && (
                            <div className="trace-recommended-action">
                              <span className="trace-recommended-action-label">✅ Recommended Action</span>
                              <p>{step.recommendation}</p>
                            </div>
                          )}
                          {step.sql && <pre className="trace-sql">{step.sql}</pre>}
                        </div>
                      )}
                    </div>
                  </li>
                );
              })}
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
                {trace.actionTargetId && !trace.isFalsePositive && (
                  <button
                    type="button"
                    className="trace-drawer-reject-trigger trace-drawer-fp-trigger"
                    disabled={submitting !== null}
                    onClick={handleMarkFalsePositive}
                  >
                    Mark as false positive
                  </button>
                )}
              </>
            )}
            {actionError && <p className="trace-drawer-error">{actionError}</p>}
          </footer>
        )}

        {/* SP-B §7: for a plain notification with no sign-off requirement
            (trace.actions !== "approve-reject") -- still worth letting a
            persona flag as wrong, since only incident/escort/critical items
            ever require sign-off, and everything else can still be noise. */}
        {trace.actions !== "approve-reject" && trace.actionTargetId && !trace.isFalsePositive && (
          <footer className="trace-drawer-footer trace-drawer-fp-footer">
            {markedFalsePositive ? (
              <p className="trace-drawer-resolved">Marked as false positive.</p>
            ) : (
              <button
                type="button"
                className="trace-drawer-reject-trigger"
                disabled={submitting !== null}
                onClick={handleMarkFalsePositive}
              >
                {submitting === "approve" ? "Marking…" : "Mark as false positive"}
              </button>
            )}
            {actionError && <p className="trace-drawer-error">{actionError}</p>}
          </footer>
        )}
      </aside>
    </>
  );
}
