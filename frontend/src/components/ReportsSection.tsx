import { useEffect, useState } from "react";
import { api } from "../api";
import type { PersonaId, ReportMeta } from "../api";
import "./ReportsSection.css";

function formatDate(ts: string): string {
  return new Date(ts).toLocaleDateString([], { month: "short", day: "numeric", year: "numeric" });
}

export function ReportsSection({ persona }: { persona: PersonaId }) {
  const [reports, setReports] = useState<ReportMeta[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    api.getReports(persona).then((res) => {
      setReports(res);
      setLoading(false);
    });
  }, [persona]);

  if (loading) return null;
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
