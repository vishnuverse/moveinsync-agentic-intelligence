import { useEffect, useState } from "react";
import { api } from "../api";
import type { TraceStep } from "../api";
import { useAppState } from "../state/AppStateContext";
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
  const { uiState, closeTrace, notifyResolved } = useAppState();
  const { trace } = uiState;
  const [steps, setSteps] = useState<TraceStep[]>([]);
  const [loading, setLoading] = useState(false);
  const [submitting, setSubmitting] = useState<"approve" | "reject" | null>(null);
  const [resolved, setResolved] = useState(false);

  useEffect(() => {
    if (!trace.open || !trace.threadId) return;
    let cancelled = false;
    setLoading(true);
    setResolved(false);
    api.getTrace(trace.threadId).then((result) => {
      if (!cancelled) {
        setSteps(result);
        setLoading(false);
      }
    });
    return () => {
      cancelled = true;
    };
  }, [trace.open, trace.threadId]);

  async function handleDecision(decision: "approve" | "reject") {
    if (!trace.actionTargetId) return;
    setSubmitting(decision);
    try {
      const res = await api.resumeNotification(trace.actionTargetId, { decision });
      setResolved(true);
      notifyResolved(trace.actionTargetId, res.status);
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
        className={`trace-drawer ${trace.open ? "trace-drawer-open" : ""}`}
        aria-hidden={!trace.open}
      >
        <header className="trace-drawer-header">
          <div>
            <p className="trace-drawer-eyebrow">How was this computed?</p>
            <h3>{trace.title ?? "Agent Trace"}</h3>
          </div>
          <button className="trace-drawer-close" onClick={closeTrace} aria-label="Close trace">
            ✕
          </button>
        </header>

        <div className="trace-drawer-body">
          {loading && <p className="trace-drawer-loading">Loading trace…</p>}
          {!loading && steps.length === 0 && (
            <p className="trace-drawer-loading">No trace available for this thread yet.</p>
          )}
          {!loading && steps.length > 0 && (
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
                  className="btn btn-danger"
                  disabled={submitting !== null}
                  onClick={() => handleDecision("reject")}
                >
                  {submitting === "reject" ? "Rejecting…" : "Reject"}
                </button>
              </>
            )}
          </footer>
        )}
      </aside>
    </>
  );
}
