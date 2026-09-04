import type {
  ActivityEntry,
  ChatMessage,
  MetricCardData,
  NotificationItem,
  PersonaId,
  ReportMeta,
  Role,
  TraceStep,
} from "./types";

export const ROLES: Role[] = [
  {
    id: "transport_manager",
    name: "Transport Manager",
    description: "Runs day-to-day fleet ops: routes, drivers, vendors, live SLA.",
  },
  {
    id: "line_manager",
    name: "Line Manager",
    description: "Owns team commute experience and attendance fairness.",
  },
  {
    id: "transport_head",
    name: "Transport Head",
    description: "Owns strategy: vendor contracts, cost, safety, sustainability.",
  },
];

function minutesAgo(mins: number): string {
  return new Date(Date.now() - mins * 60_000).toISOString();
}

const SQL_ON_TIME = `SELECT r.route_name,
       COUNT(*) FILTER (
         WHERE t.actual_arrival <= t.scheduled_arrival + INTERVAL '5 minutes'
       ) * 100.0 / COUNT(*) AS on_time_pct
FROM route_trips t
JOIN routes r ON r.id = t.route_id
WHERE t.scheduled_arrival >= NOW() - INTERVAL '7 days'
GROUP BY r.route_name
ORDER BY on_time_pct ASC
LIMIT 10;`;

const SQL_COST = `SELECT v.name AS vendor_name,
       SUM(rc.total_cost) / NULLIF(SUM(rc.passenger_km), 0) AS cost_per_pkm
FROM route_costs rc
JOIN vendors v ON v.id = rc.vendor_id
WHERE rc.trip_date >= date_trunc('month', CURRENT_DATE)
GROUP BY v.name
ORDER BY cost_per_pkm DESC;`;

const SQL_SAFETY = `SELECT s.id, s.trip_id, s.incident_type, s.severity, s.occurred_at
FROM safety_incidents s
JOIN route_trips t ON t.id = s.trip_id
JOIN routes r ON r.id = t.route_id
WHERE s.occurred_at >= NOW() - INTERVAL '7 days'
ORDER BY s.occurred_at DESC;`;

const SQL_VENDOR_SLA = `SELECT v.name AS vendor_name,
       COUNT(*) FILTER (WHERE vi.sla_met) * 100.0 / COUNT(*) AS sla_pct
FROM vendor_invoices vi
JOIN vendors v ON v.id = vi.vendor_id
WHERE vi.billing_period = to_char(CURRENT_DATE, 'YYYY-MM')
GROUP BY v.name
HAVING COUNT(*) FILTER (WHERE vi.sla_met) * 100.0 / COUNT(*) < 95
ORDER BY sla_pct ASC;`;

const SQL_CARBON = `SELECT date_trunc('month', e.recorded_at) AS month,
       SUM(e.co2_grams) / NULLIF(SUM(e.passenger_km), 0) AS gco2_per_pkm
FROM emissions_log e
WHERE e.recorded_at >= CURRENT_DATE - INTERVAL '6 months'
GROUP BY month
ORDER BY month;`;

const SQL_ATTENDANCE = `SELECT emp.id, emp.name, a.status,
       (c.actual_arrival - c.scheduled_arrival) AS shuttle_delay
FROM attendance_records a
JOIN employees emp ON emp.id = a.employee_id
JOIN commute_logs c
  ON c.employee_id = a.employee_id AND c.trip_date = a.attendance_date
WHERE a.status = 'late'
  AND emp.line_manager_id = :manager_id
  AND a.attendance_date = CURRENT_DATE;`;

type TraceCategory =
  | "ontime"
  | "cost"
  | "safety"
  | "vendorsla"
  | "carbon"
  | "attendance"
  | "digest"
  | "report"
  | "alerts"
  | "general";

interface TraceTemplate {
  signal: string;
  sql?: string;
  sqlSummary: string;
  context: string;
  decision: string;
  retryCount?: number;
}

const TRACE_TEMPLATES: Record<TraceCategory, TraceTemplate> = {
  ontime: {
    signal:
      "route_trips delta detected: 3 consecutive days below the 95% on-time SLA threshold on a monitored route.",
    sql: SQL_ON_TIME,
    sqlSummary: "Returned 10 rows in 84ms against the read-only replica.",
    context:
      "Compared against the org's 95% on-time SLA target (92% is the actionable-breach floor). Trend framed against the last 7 days, not a single snapshot.",
    decision:
      "Flagged as an actionable SLA breach; root cause hint attached (driver shift-change overlap window). Routed to the persona dashboard as a warning-severity metric.",
  },
  cost: {
    signal: "route_costs / vendor_invoices delta detected for the current billing month.",
    sql: SQL_COST,
    sqlSummary: "Returned 6 rows (one per active vendor) in 61ms.",
    context:
      "Benchmarked against the ₹12–18 per passenger-km industry band for corporate shuttle service. Value sits inside the band but trending toward the ceiling.",
    decision:
      "No sign-off required — logged as a watch-list trend, surfaced with context so it reads as 'why it matters', not just a number.",
  },
  safety: {
    signal: "New row in safety_incidents joined against route_trips for the affected route.",
    sql: SQL_SAFETY,
    sqlSummary: "Returned 2 rows in 39ms.",
    context:
      "Any incident is treated as high-severity by policy; impact framing adds driver/vendor identity and whether it's a repeat pattern for that vendor.",
    decision:
      "Escalation drafted for driver/vendor notification. Routed through the interrupt gate — held for human sign-off before any message is sent.",
    retryCount: 0,
  },
  vendorsla: {
    signal: "vendor_invoices aggregation detected a vendor dropping below the 92% actionable-breach threshold.",
    sql: SQL_VENDOR_SLA,
    sqlSummary: "Returned 2 rows (vendors below threshold) in 55ms. Self-corrected once on a GROUP BY/HAVING ordering issue.",
    context:
      "Compared against the 95% contracted SLA and the 92% actionable-breach floor from the benchmark table, not just the vendor's own history.",
    decision:
      "Contract review escalation drafted for procurement. Routed through the interrupt gate — held for human sign-off.",
    retryCount: 1,
  },
  carbon: {
    signal: "emissions_log aggregation recomputed for the trailing 6-month window.",
    sql: SQL_CARBON,
    sqlSummary: "Returned 6 rows (one per month) in 72ms.",
    context:
      "Benchmarked against the 82 gCO2/passenger-km ICE-fleet baseline and this quarter's configured sustainability target, via the research agent's curated benchmark lookup.",
    decision:
      "Logged as on-track; no action required. Surfaced with the benchmark comparison so the number reads as 'good' rather than being judged in isolation.",
  },
  attendance: {
    signal: "attendance_records joined against commute_logs found late clock-ins correlated with a shuttle delay window.",
    sql: SQL_ATTENDANCE,
    sqlSummary: "Returned 3 rows (affected team members) in 47ms.",
    context:
      "Cross-referenced against the shuttle's actual vs. scheduled arrival for the same trip, to separate transport-caused lateness from personal lateness.",
    decision:
      "Attendance correction note drafted for HR. Routed through the interrupt gate — held for the line manager's sign-off before it reaches HR.",
  },
  digest: {
    signal: "Scheduled weekly digest trigger fired (APScheduler tick, no user action).",
    sqlSummary: "Aggregated team-level on-time, safety, and cost metrics over the trailing 7 days.",
    context: "Digest content reuses the same impact-context builder as the live dashboard cards, so the narrative and the metrics never disagree.",
    decision: "Digest compiled and stored as ready-to-review; no sign-off required for a read-only summary.",
  },
  report: {
    signal: "Scheduled monthly/quarterly report trigger fired (APScheduler tick, no user action).",
    sqlSummary: "Aggregated cost, safety, vendor, and carbon facts across the full reporting period.",
    context: "Report narrative generated from the same grounded facts and benchmarks used elsewhere, so figures are traceable back to source queries.",
    decision: "Brand-styled HTML report generated and stored; ready to forward without further edits.",
  },
  alerts: {
    signal: "Notification inbox aggregation across open and needs-intervention items for this persona.",
    sqlSummary: "Read directly from agent_notifications, no ad-hoc query needed.",
    context: "Counted separately from acknowledged items so the metric reflects outstanding attention, not total history.",
    decision: "Surfaced as an at-a-glance count; each underlying item carries its own trace.",
  },
  general: {
    signal: "Signal aggregated from the relevant fact table(s) for this question.",
    sqlSummary: "Query executed against the read-only replica with a LIMIT and statement timeout enforced.",
    context: "Framed against the relevant benchmark or the persona's own historical trend.",
    decision: "Answer composed from the grounded query result plus the attached context.",
  },
};

export function buildTrace(category: TraceCategory, baseMinutesAgo = 6): TraceStep[] {
  const t = TRACE_TEMPLATES[category];
  const steps: TraceStep[] = [
    {
      step: "signal_detected",
      label: "Signal Detected",
      detail: t.signal,
      timestamp: minutesAgo(baseMinutesAgo),
    },
  ];
  if (t.sql) {
    steps.push({
      step: "sql_generated",
      label: "SQL Generated",
      detail: "Text-to-SQL agent grounded on live schema DDL + sample rows, chain-of-thought prompted.",
      sql: t.sql,
      timestamp: minutesAgo(baseMinutesAgo - 1),
    });
  }
  steps.push({
    step: "sql_executed",
    label: "SQL Executed",
    detail: t.sqlSummary,
    retry_count: t.retryCount ?? 0,
    timestamp: minutesAgo(baseMinutesAgo - 2),
  });
  steps.push({
    step: "context_built",
    label: "Impact Context Attached",
    detail: t.context,
    timestamp: minutesAgo(baseMinutesAgo - 4),
  });
  steps.push({
    step: "decision",
    label: "Decision",
    detail: t.decision,
    timestamp: minutesAgo(baseMinutesAgo - 5),
  });
  return steps;
}

export const TRACE_LIBRARY: Record<string, TraceStep[]> = {
  "tm-trace-ontime": buildTrace("ontime", 42),
  "tm-trace-cost": buildTrace("cost", 95),
  "tm-trace-safety": buildTrace("safety", 18),
  "tm-trace-vendorsla": buildTrace("vendorsla", 130),
  "tm-trace-alerts": buildTrace("alerts", 5),

  "lm-trace-ontime": buildTrace("ontime", 51),
  "lm-trace-attendance": buildTrace("attendance", 22),
  "lm-trace-safety": buildTrace("safety", 260),
  "lm-trace-cost": buildTrace("cost", 88),
  "lm-trace-digest": buildTrace("digest", 12),

  "th-trace-ontime": buildTrace("ontime", 70),
  "th-trace-cost": buildTrace("cost", 140),
  "th-trace-carbon": buildTrace("carbon", 200),
  "th-trace-vendor": buildTrace("vendorsla", 300),
  "th-trace-safety": buildTrace("safety", 400),
  "th-trace-report": buildTrace("report", 600),
};

export function dashboardFor(persona: PersonaId): MetricCardData[] {
  if (persona === "transport_manager") {
    return [
      {
        id: "on_time_pct",
        label: "On-Time Arrival",
        value: "91.2%",
        trend: "down",
        severity: "warning",
        context_note:
          "Below the 95% SLA target for 3 straight days on Route 14 (Whitefield–Electronic City); driver shift-change overlap is the likely cause.",
        thread_id: "tm-trace-ontime",
      },
      {
        id: "cost_per_km",
        label: "Cost per Passenger-KM",
        value: "₹16.80",
        trend: "up",
        severity: "warning",
        context_note:
          "Up from ₹14.20 last month. Still inside the ₹12–18 industry band but trending toward the ceiling — fuel surcharge from the Metro Cabs vendor.",
        thread_id: "tm-trace-cost",
      },
      {
        id: "safety_incidents",
        label: "Safety Incidents (7d)",
        value: "2",
        trend: "up",
        severity: "critical",
        context_note:
          "Both incidents logged on Route 14: one harsh-braking event flagged for driver coaching, one minor collision under review.",
        thread_id: "tm-trace-safety",
      },
      {
        id: "vendor_sla",
        label: "Vendor SLA Compliance",
        value: "88%",
        trend: "down",
        severity: "warning",
        context_note:
          "Metro Cabs fell to 88% against a 95% contracted SLA — three late dispatches this week traced to a single depot.",
        thread_id: "tm-trace-vendorsla",
      },
      {
        id: "active_alerts",
        label: "Open Alerts Needing Action",
        value: "3",
        trend: "flat",
        severity: "warning",
        context_note:
          "3 unresolved notifications in your inbox, including 1 incident escalation awaiting your sign-off.",
        thread_id: "tm-trace-alerts",
      },
    ];
  }
  if (persona === "line_manager") {
    return [
      {
        id: "team_on_time_pct",
        label: "Team On-Time Arrival",
        value: "93.5%",
        trend: "down",
        severity: "warning",
        context_note:
          "4 of your 28 team members were on a delayed shuttle twice this week — worth flagging before it affects attendance records.",
        thread_id: "lm-trace-ontime",
      },
      {
        id: "attendance_flags",
        label: "Attendance Flags Linked to Transport",
        value: "5",
        trend: "up",
        severity: "warning",
        context_note:
          "5 late clock-ins this week correlate directly with shuttle delays on Route 14, not personal lateness — protects your team from unfair marks.",
        thread_id: "lm-trace-attendance",
      },
      {
        id: "team_safety_incidents",
        label: "Team Safety Incidents (30d)",
        value: "1",
        trend: "flat",
        severity: "critical",
        context_note:
          "One harsh-braking event involved 2 of your team members; both confirmed unharmed, coaching note filed against the driver.",
        thread_id: "lm-trace-safety",
      },
      {
        id: "team_commute_cost",
        label: "Team Monthly Commute Cost",
        value: "₹1.84L",
        trend: "up",
        severity: "neutral",
        context_note:
          "Tracking 4% above last month due to 2 new joiners added to the West Zone route — within budget.",
        thread_id: "lm-trace-cost",
      },
      {
        id: "digest_pending",
        label: "Weekly Digest",
        value: "Ready",
        trend: "flat",
        severity: "neutral",
        context_note:
          "This week's team commute-attendance digest is generated and ready to review before Friday's stand-up.",
        thread_id: "lm-trace-digest",
      },
    ];
  }
  return [
    {
      id: "fleet_on_time_pct",
      label: "Fleet-Wide On-Time Arrival",
      value: "92.1%",
      trend: "down",
      severity: "warning",
      context_note:
        "Below the 95% SLA target org-wide; South and West zones are dragging the average down, driven by 2 underperforming vendors.",
      thread_id: "th-trace-ontime",
    },
    {
      id: "cost_per_km_fleet",
      label: "Fleet Cost per Passenger-KM",
      value: "₹15.40",
      trend: "flat",
      severity: "good",
      context_note:
        "Within the ₹12–18/km industry benchmark band; stable for 2 consecutive quarters despite fuel price volatility.",
      thread_id: "th-trace-cost",
    },
    {
      id: "carbon_emissions",
      label: "Carbon Emissions",
      value: "74 gCO2/pkm",
      trend: "down",
      severity: "good",
      context_note:
        "9.8% below the 82 gCO2/pkm ICE-fleet baseline, driven by the EV pilot on 3 routes — on track against this quarter's sustainability target.",
      thread_id: "th-trace-carbon",
    },
    {
      id: "vendor_scorecard_avg",
      label: "Avg Vendor SLA Score",
      value: "91%",
      trend: "down",
      severity: "warning",
      context_note:
        "2 of 6 vendors (Metro Cabs, QuickRide) are below the 92% actionable-breach threshold this quarter, both flagged for contract review.",
      thread_id: "th-trace-vendor",
    },
    {
      id: "safety_incidents_fleet",
      label: "Safety Incidents (Quarter)",
      value: "7",
      trend: "up",
      severity: "critical",
      context_note:
        "Up from 4 last quarter; concentrated in 2 vendors — a pattern worth raising at the next vendor governance review.",
      thread_id: "th-trace-safety",
    },
  ];
}

export function initialNotifications(persona: PersonaId): NotificationItem[] {
  if (persona === "transport_manager") {
    return [
      {
        id: "tm-n1",
        severity: "critical",
        message:
          "Safety incident on Route 14 (Whitefield–Electronic City): harsh-braking event at 08:14. Driver notification drafted — awaiting your sign-off before it's sent to the vendor.",
        status: "needs-intervention",
        thread_id: "tm-trace-safety",
        created_at: minutesAgo(18),
      },
      {
        id: "tm-n2",
        severity: "warning",
        message:
          "Route 14 breached the 95% on-time SLA for the 3rd straight day. Vendor reallocation recommended — see trace for the cost/SLA delta.",
        status: "open",
        thread_id: "tm-trace-ontime",
        created_at: minutesAgo(42),
      },
      {
        id: "tm-n3",
        severity: "info",
        message: "Metro Cabs invoice for this month reconciled automatically — no discrepancies found.",
        status: "acked",
        thread_id: "tm-trace-cost",
        created_at: minutesAgo(180),
      },
    ];
  }
  if (persona === "line_manager") {
    return [
      {
        id: "lm-n1",
        severity: "warning",
        message:
          "3 of your team members were marked late today due to a 22-minute shuttle delay on Route 14 — attendance correction note drafted for HR, awaiting your sign-off.",
        status: "needs-intervention",
        thread_id: "lm-trace-attendance",
        created_at: minutesAgo(22),
      },
      {
        id: "lm-n2",
        severity: "info",
        message: "Weekly team commute-attendance digest generated and ready for review.",
        status: "open",
        thread_id: "lm-trace-digest",
        created_at: minutesAgo(12),
      },
      {
        id: "lm-n3",
        severity: "warning",
        message: "Safety incident affecting 2 of your team members acknowledged and logged.",
        status: "acked",
        thread_id: "lm-trace-safety",
        created_at: minutesAgo(260),
      },
    ];
  }
  return [
    {
      id: "th-n1",
      severity: "critical",
      message:
        "Metro Cabs SLA has dropped below the 92% actionable-breach threshold for 2 consecutive months. Contract review escalation drafted — awaiting your sign-off before it's sent to procurement.",
      status: "needs-intervention",
      thread_id: "th-trace-vendor",
      created_at: minutesAgo(300),
    },
    {
      id: "th-n2",
      severity: "info",
      message: "Q3 leadership report generated and ready to forward to the board.",
      status: "open",
      thread_id: "th-trace-report",
      created_at: minutesAgo(600),
    },
    {
      id: "th-n3",
      severity: "warning",
      message: "Carbon emissions trend acknowledged — on track against this quarter's sustainability target.",
      status: "acked",
      thread_id: "th-trace-carbon",
      created_at: minutesAgo(200),
    },
  ];
}

export function reportsFor(persona: PersonaId): ReportMeta[] {
  if (persona === "transport_manager") {
    return [
      {
        id: "tm-r1",
        title: "Daily Ops Digest — Sep 3",
        period: "2026-09-03",
        generated_at: minutesAgo(720),
        preview_url: "/reports/tm-r1.html",
      },
      {
        id: "tm-r2",
        title: "Daily Ops Digest — Sep 2",
        period: "2026-09-02",
        generated_at: minutesAgo(1900),
        preview_url: "/reports/tm-r2.html",
      },
    ];
  }
  if (persona === "line_manager") {
    return [
      {
        id: "lm-r1",
        title: "Weekly Team Commute Digest — Week 35",
        period: "2026-08-25 to 2026-08-31",
        generated_at: minutesAgo(12),
        preview_url: "/reports/lm-r1.html",
      },
    ];
  }
  return [
    {
      id: "th-r1",
      title: "Q3 Leadership Report — Cost, Safety, Vendor & Carbon",
      period: "Q3 2026",
      generated_at: minutesAgo(600),
      preview_url: "/reports/th-r1.html",
    },
    {
      id: "th-r2",
      title: "Q2 Leadership Report — Cost, Safety, Vendor & Carbon",
      period: "Q2 2026",
      generated_at: minutesAgo(60_000),
      preview_url: "/reports/th-r2.html",
    },
  ];
}

export function initialChatHistory(persona: PersonaId): ChatMessage[] {
  const greetings: Record<PersonaId, string> = {
    transport_manager:
      "Hi, I'm your fleet operations assistant. Ask me about routes, delays, vendors, or safety incidents.",
    line_manager:
      "Hi, I'm your team commute assistant. Ask me about your team's attendance, safety, or transport costs.",
    transport_head:
      "Hi, I'm your strategic transport assistant. Ask me about vendor performance, cost trends, or sustainability.",
  };
  return [
    {
      id: `${persona}-greet`,
      role: "agent",
      text: greetings[persona],
      created_at: minutesAgo(1440),
    },
  ];
}

const RAW_ACTIVITY_LOG: ActivityEntry[] = [
  {
    id: "act-1",
    persona: "transport_manager",
    action: "Detected SLA breach on Route 14 and generated a vendor reallocation recommendation.",
    timestamp: minutesAgo(42),
    triggered_by: "event",
  },
  {
    id: "act-2",
    persona: "transport_manager",
    action: "Safety incident detected; escalation drafted and held at the interrupt gate for sign-off.",
    timestamp: minutesAgo(18),
    triggered_by: "event",
  },
  {
    id: "act-3",
    persona: "line_manager",
    action: "Correlated late clock-ins with a shuttle delay; attendance correction drafted for HR sign-off.",
    timestamp: minutesAgo(22),
    triggered_by: "event",
  },
  {
    id: "act-4",
    persona: "line_manager",
    action: "Weekly team commute-attendance digest compiled on schedule.",
    timestamp: minutesAgo(12),
    triggered_by: "schedule",
  },
  {
    id: "act-5",
    persona: "transport_head",
    action: "Vendor SLA aggregation flagged Metro Cabs below the actionable-breach threshold for the 2nd month running.",
    timestamp: minutesAgo(300),
    triggered_by: "schedule",
  },
  {
    id: "act-6",
    persona: "transport_head",
    action: "Q3 leadership report generated and stored, combining cost, safety, vendor, and carbon facts.",
    timestamp: minutesAgo(600),
    triggered_by: "schedule",
  },
  {
    id: "act-7",
    persona: "transport_manager",
    action: "Metro Cabs invoice reconciled automatically against contracted rates — no discrepancies.",
    timestamp: minutesAgo(180),
    triggered_by: "schedule",
  },
  {
    id: "act-8",
    persona: "transport_head",
    action: "Carbon emissions trend recomputed against the ICE-fleet baseline and this quarter's target.",
    timestamp: minutesAgo(200),
    triggered_by: "schedule",
  },
  {
    id: "act-9",
    persona: "line_manager",
    action: "Safety incident acknowledgement logged for 2 affected team members.",
    timestamp: minutesAgo(260),
    triggered_by: "event",
  },
  {
    id: "act-10",
    persona: "transport_manager",
    action: "Postgres LISTEN/NOTIFY picked up a new route_trips row and re-ran the delay detector.",
    timestamp: minutesAgo(6),
    triggered_by: "event",
  },
];

export const ACTIVITY_LOG: ActivityEntry[] = [...RAW_ACTIVITY_LOG].sort(
  (a, b) => new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime(),
);
