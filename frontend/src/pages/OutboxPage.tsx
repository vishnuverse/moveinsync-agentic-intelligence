import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "../api";
import type { NotificationItem, NotificationSeverity, PersonaId, ReportMeta } from "../api";
import { useAppState } from "../state/AppStateContext";
import { EmptyState, ErrorState, LoadingState } from "../components/AsyncStatus";
import { IconAlert, IconBolt, IconClock } from "../components/icons";
import { withTimeout } from "../lib/timeout";
import "./OutboxPage.css";

// --- Local severity -> color/icon/label map, mirrored from LiveEventFeed
// (SP-A) so the Outbox reads as the same visual system: same Carbon-ish
// literals, icon + text label on every chip (never color alone). Kept LOCAL
// to this feature on purpose, exactly like the live feed, so the shared
// color-system work can't collide with it. (SP-C may later fold both into the
// --color-critical/-warning/-good/-info/-neutral tokens.)
type Tone = "critical" | "warning" | "info" | "success" | "neutral";

const TONE_STYLE: Record<Tone, { bg: string; fg: string }> = {
  critical: { bg: "#da1e28", fg: "#ffffff" },
  warning: { bg: "#f1c21b", fg: "#1b1b1b" }, // dark text on yellow (contrast)
  info: { bg: "#0043ce", fg: "#ffffff" },
  success: { bg: "#24a148", fg: "#ffffff" },
  neutral: { bg: "#6f6f6f", fg: "#ffffff" },
};

const SEVERITY_TONE: Record<NotificationSeverity, Tone> = {
  critical: "critical",
  warning: "warning",
  info: "info",
};

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

function SeverityChip({ tone }: { tone: Tone }) {
  const style = TONE_STYLE[tone];
  return (
    <span className="outbox-chip" style={{ background: style.bg, color: style.fg }}>
      <SeverityIcon tone={tone} />
      {SEVERITY_LABEL[tone]}
    </span>
  );
}

const PERSONA_LABEL: Record<PersonaId, string> = {
  transport_manager: "Transport Manager",
  line_manager: "Line Manager",
  transport_head: "Transport Head",
};

const NOTIFICATION_STATUS_LABEL: Record<NotificationItem["status"], string> = {
  open: "Drafted",
  acked: "Acknowledged",
  "needs-intervention": "Awaiting sign-off",
};

function formatDate(ts: string): string {
  return new Date(ts).toLocaleDateString([], { month: "short", day: "numeric", year: "numeric" });
}

function formatTime(ts: string): string {
  return new Date(ts).toLocaleString([], {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

// --- LOCAL/simulated "sent" ledger. Purely client-side: nothing is ever
// dispatched anywhere in this demo. Keyed by persona so a "sent" mark can't
// bleed across personas, and each communication is keyed by its notification
// id. Wrapped in try/catch throughout (private browsing / quota / disabled
// storage all degrade to "nothing is marked sent" rather than throwing).
const SENT_STORAGE_KEY = "moveinsync.outbox.sent.v1";

type SentLedger = Record<string, Record<string, string>>; // persona -> { commId: sentAtISO }

function loadSentLedger(): SentLedger {
  try {
    const raw = localStorage.getItem(SENT_STORAGE_KEY);
    if (!raw) return {};
    const parsed = JSON.parse(raw);
    return parsed && typeof parsed === "object" ? (parsed as SentLedger) : {};
  } catch {
    return {};
  }
}

function saveSentLedger(ledger: SentLedger): void {
  try {
    localStorage.setItem(SENT_STORAGE_KEY, JSON.stringify(ledger));
  } catch {
    // best-effort persistence -- fine to drop in private browsing / over quota
  }
}

// Small helper: derive a filename-safe slug for the downloaded memo.
function slugify(text: string): string {
  return (
    text
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-+|-+$/g, "")
      .slice(0, 60) || "communication"
  );
}

// A drafted communication, as derived from one notification.
function buildMemoMarkdown(
  comm: NotificationItem,
  persona: PersonaId,
  sentAt: string | null,
): string {
  const lines = [
    `# ${comm.message.length > 80 ? `${comm.message.slice(0, 77)}…` : comm.message}`,
    "",
    `- **Persona:** ${PERSONA_LABEL[persona]}`,
    `- **Severity:** ${comm.severity}`,
    `- **Scope:** notification ${comm.id}`,
    `- **Drafted:** ${formatTime(comm.created_at)}`,
    `- **Status:** ${sentAt ? `Sent (simulated) ${formatTime(sentAt)}` : NOTIFICATION_STATUS_LABEL[comm.status]}`,
    "",
    "## Message",
    "",
    comm.message,
    "",
    "---",
    "",
    "_Drafted by the MoveInSync agent. This is a local demo memo — sending is",
    "simulated and no message was dispatched to any external recipient._",
    "",
  ];
  return lines.join("\n");
}

function downloadBlob(filename: string, content: string, type: string): void {
  const blob = new Blob([content], { type });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  // Revoke on the next tick so the click has committed to the download first.
  setTimeout(() => URL.revokeObjectURL(url), 0);
}

// A tiny transient "copied ✓ / downloaded ✓" acknowledgement per-action.
function useFlash(): [string | null, (msg: string) => void] {
  const [flash, setFlash] = useState<string | null>(null);
  const show = useCallback((msg: string) => {
    setFlash(msg);
    window.setTimeout(() => setFlash((cur) => (cur === msg ? null : cur)), 1600);
  }, []);
  return [flash, show];
}

async function copyText(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    // fall through to the legacy path
  }
  try {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    const ok = document.execCommand("copy");
    document.body.removeChild(ta);
    return ok;
  } catch {
    return false;
  }
}

function ReportRow({ report }: { report: ReportMeta }) {
  const [flash, showFlash] = useFlash();

  async function handleCopyLink() {
    // Copy an absolute URL so the pasted link is usable outside this tab.
    const href = new URL(report.preview_url, window.location.origin).toString();
    const ok = await copyText(href);
    showFlash(ok ? "Link copied ✓" : "Copy failed");
  }

  return (
    <div className="outbox-card">
      <div className="outbox-card-main">
        <p className="outbox-card-title">{report.title}</p>
        <p className="outbox-card-meta">
          {report.period} · generated {formatDate(report.generated_at)}
        </p>
      </div>
      <div className="outbox-actions">
        {flash && (
          <span className="outbox-flash" role="status">
            {flash}
          </span>
        )}
        <a className="btn btn-secondary outbox-btn" href={report.preview_url} target="_blank" rel="noreferrer">
          Preview ↗
        </a>
        <button type="button" className="btn btn-secondary outbox-btn" onClick={handleCopyLink}>
          Copy link
        </button>
      </div>
    </div>
  );
}

function CommunicationRow({
  comm,
  persona,
  sentAt,
  onMarkSent,
}: {
  comm: NotificationItem;
  persona: PersonaId;
  sentAt: string | null;
  onMarkSent: (id: string) => void;
}) {
  const [flash, showFlash] = useFlash();
  const tone = SEVERITY_TONE[comm.severity];

  async function handleCopy() {
    const ok = await copyText(comm.message);
    showFlash(ok ? "Copied ✓" : "Copy failed");
  }

  function handleDownload() {
    const md = buildMemoMarkdown(comm, persona, sentAt);
    downloadBlob(`memo-${slugify(comm.message)}.md`, md, "text/markdown;charset=utf-8");
    showFlash("Downloaded ✓");
  }

  return (
    <div className="outbox-card outbox-comm">
      <div className="outbox-card-main">
        <div className="outbox-card-top">
          <SeverityChip tone={tone} />
          {sentAt ? (
            <span className="outbox-status outbox-status-sent">Sent ✓ (simulated)</span>
          ) : (
            <span className="outbox-status">{NOTIFICATION_STATUS_LABEL[comm.status]}</span>
          )}
          <span className="outbox-card-time">{formatTime(comm.created_at)}</span>
        </div>
        <p className="outbox-comm-body">{comm.message}</p>
        {sentAt && (
          <p className="outbox-sent-hint">
            In production this would email the vendor's dispatch desk.
          </p>
        )}
      </div>
      <div className="outbox-actions">
        {flash && (
          <span className="outbox-flash" role="status">
            {flash}
          </span>
        )}
        <button type="button" className="btn btn-secondary outbox-btn" onClick={handleCopy}>
          Copy
        </button>
        <button type="button" className="btn btn-secondary outbox-btn" onClick={handleDownload}>
          Download .md
        </button>
        <button
          type="button"
          className={`btn outbox-btn ${sentAt ? "btn-secondary" : "btn-primary"}`}
          onClick={() => onMarkSent(comm.id)}
          disabled={!!sentAt}
        >
          {sentAt ? "Sent ✓" : "Mark as sent"}
        </button>
      </div>
    </div>
  );
}

export function OutboxPage() {
  const { persona } = useAppState();
  const [reports, setReports] = useState<ReportMeta[]>([]);
  const [comms, setComms] = useState<NotificationItem[]>([]);
  const [status, setStatus] = useState<"loading" | "ready" | "error">("loading");
  const [sentLedger, setSentLedger] = useState<SentLedger>(loadSentLedger);

  const load = useCallback(() => {
    setStatus("loading");
    Promise.all([withTimeout(api.getReports(persona)), withTimeout(api.getNotifications(persona))])
      .then(([reps, notifs]) => {
        setReports(reps);
        setComms(notifs);
        setStatus("ready");
      })
      .catch(() => setStatus("error"));
  }, [persona]);

  useEffect(() => {
    load();
  }, [load]);

  const sentForPersona = useMemo(() => sentLedger[persona] ?? {}, [sentLedger, persona]);

  const markSent = useCallback(
    (id: string) => {
      setSentLedger((prev) => {
        const forPersona = { ...(prev[persona] ?? {}) };
        if (forPersona[id]) return prev; // already sent -- no-op
        forPersona[id] = new Date().toISOString();
        const next = { ...prev, [persona]: forPersona };
        saveSentLedger(next);
        return next;
      });
    },
    [persona],
  );

  return (
    <div>
      <div className="dashboard-heading">
        <h2>Outbox</h2>
        <p>
          Everything the agent has produced and <em>would send</em> for the{" "}
          {PERSONA_LABEL[persona]} — generated reports and drafted vendor/leadership
          communications, in one place.
        </p>
      </div>

      <p className="outbox-sim-caption" role="note">
        Sending is <strong>simulated</strong> for this local demo — no email, Slack, or SMS
        leaves this browser. “Mark as sent” only flips a local status badge.
      </p>

      {status === "loading" && <LoadingState label="Loading outbox…" />}
      {status === "error" && <ErrorState label="Couldn't load the outbox." onRetry={load} />}

      {status === "ready" && (
        <>
          <section className="outbox-section">
            <h3 className="outbox-section-heading">
              Reports<span className="outbox-count">{reports.length}</span>
            </h3>
            {reports.length === 0 ? (
              <EmptyState label="No reports generated for this persona yet." />
            ) : (
              <div className="outbox-list">
                {reports.map((report) => (
                  <ReportRow key={report.id} report={report} />
                ))}
              </div>
            )}
          </section>

          <section className="outbox-section">
            <h3 className="outbox-section-heading">
              Communications<span className="outbox-count">{comms.length}</span>
            </h3>
            {comms.length === 0 ? (
              <EmptyState label="No drafted communications for this persona right now." />
            ) : (
              <div className="outbox-list">
                {comms.map((comm) => (
                  <CommunicationRow
                    key={comm.id}
                    comm={comm}
                    persona={persona}
                    sentAt={sentForPersona[comm.id] ?? null}
                    onMarkSent={markSent}
                  />
                ))}
              </div>
            )}
          </section>
        </>
      )}
    </div>
  );
}
