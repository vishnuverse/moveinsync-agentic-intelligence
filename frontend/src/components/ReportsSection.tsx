import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import type { PersonaId, ReportMeta } from "../api";
import { withTimeout } from "../lib/timeout";
import { ErrorState } from "./AsyncStatus";
import "./ReportsSection.css";

function formatDate(ts: string): string {
  return new Date(ts).toLocaleDateString([], { month: "short", day: "numeric", year: "numeric" });
}

export function ReportsSection({ persona }: { persona: PersonaId }) {
  const [reports, setReports] = useState<ReportMeta[]>([]);
  const [status, setStatus] = useState<"loading" | "ready" | "error">("loading");

  const load = useCallback(() => {
    setStatus("loading");
    withTimeout(api.getReports(persona))
      .then((res) => {
        setReports(res);
        setStatus("ready");
      })
      .catch(() => setStatus("error"));
  }, [persona]);

  useEffect(() => {
    load();
  }, [load]);

  if (status === "loading") return null;
  // This section is supplementary to the dashboard above it, so a failed
  // fetch gets a compact inline retry rather than a full-height error state
  // competing with the metrics/charts for attention.
  if (status === "error") {
    return (
      <section className="reports-section">
        <h3 className="reports-section-heading">Generated Reports</h3>
        <ErrorState label="Couldn't load reports." onRetry={load} />
      </section>
    );
  }
  if (reports.length === 0) return null;

  return (
    <section className="reports-section">
      <h3 className="reports-section-heading">Generated Reports</h3>
      <div className="reports-list">
        {reports.map((report) => (
          <a
            key={report.id}
            className="report-card"
            href={report.preview_url}
            target="_blank"
            rel="noreferrer"
          >
            <div>
              <p className="report-card-title">{report.title}</p>
              <p className="report-card-meta">
                {report.period} · generated {formatDate(report.generated_at)}
              </p>
            </div>
            <span className="link-btn">Open ↗</span>
          </a>
        ))}
      </div>
    </section>
  );
}
