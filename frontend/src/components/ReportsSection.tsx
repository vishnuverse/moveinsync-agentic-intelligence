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
  const [generating, setGenerating] = useState(false);
  const [genError, setGenError] = useState<string | null>(null);

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

  const handleGenerate = useCallback(async () => {
    setGenerating(true);
    setGenError(null);
    try {
      // Report generation can take longer than a plain fetch, so give it a
      // roomier ceiling than withTimeout's 20s default.
      await withTimeout(api.generateReport(persona), 60000);
      load();
    } catch (err) {
      setGenError(err instanceof Error ? err.message : "Couldn't generate the report.");
    } finally {
      setGenerating(false);
    }
  }, [persona, load]);

  return (
    <section className="reports-section">
      <div className="reports-section-header">
        <h3 className="reports-section-heading">Generated Reports</h3>
        <button
          type="button"
          className="btn btn-primary reports-generate-btn"
          onClick={handleGenerate}
          disabled={generating}
        >
          {generating ? "Generating…" : "Generate report"}
        </button>
      </div>

      {genError && <p className="reports-generate-error" role="status">{genError}</p>}

      {/* Supplementary to the dashboard above, so a failed *list* fetch gets a
          compact inline retry rather than a full-height error state. */}
      {status === "error" && <ErrorState label="Couldn't load reports." onRetry={load} />}

      {status === "ready" && reports.length === 0 && (
        <p className="reports-section-empty">
          No reports yet. Press “Generate report” to create one.
        </p>
      )}

      {status === "ready" && reports.length > 0 && (
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
      )}
    </section>
  );
}
