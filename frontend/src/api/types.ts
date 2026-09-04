export type PersonaId = "transport_manager" | "line_manager" | "transport_head";

export interface Role {
  id: PersonaId;
  name: string;
  description: string;
}

export type MetricSeverity = "good" | "warning" | "critical" | "neutral";
export type MetricTrend = "up" | "down" | "flat";

export interface MetricCardData {
  id: string;
  label: string;
  value: string;
  trend: MetricTrend;
  severity: MetricSeverity;
  context_note: string;
  thread_id: string;
}

export type NotificationSeverity = "info" | "warning" | "critical";
export type NotificationStatus = "open" | "acked" | "needs-intervention";

export interface NotificationItem {
  id: string;
  severity: NotificationSeverity;
  message: string;
  status: NotificationStatus;
  thread_id: string;
  created_at: string;
}

export interface ResumeDecisionRequest {
  decision: "approve" | "reject";
  edited_text?: string;
}

export interface ResumeDecisionResponse {
  id: string;
  status: NotificationStatus;
  resolved_at: string;
}

export type TraceStepType =
  | "signal_detected"
  | "sql_generated"
  | "sql_executed"
  | "context_built"
  | "decision";

export interface TraceStep {
  step: TraceStepType;
  label: string;
  detail: string;
  sql?: string;
  retry_count?: number;
  timestamp: string;
}

export interface ReportMeta {
  id: string;
  title: string;
  period: string;
  generated_at: string;
  preview_url: string;
}

export interface ChatMessage {
  id: string;
  role: "user" | "agent";
  text: string;
  thread_id?: string;
  created_at: string;
}

export interface ChatRequest {
  persona: PersonaId;
  message: string;
}

export interface ChatResponse {
  message: ChatMessage;
}

export type ActivityTrigger = "schedule" | "event";

export interface ActivityEntry {
  id: string;
  persona: PersonaId;
  action: string;
  timestamp: string;
  triggered_by: ActivityTrigger;
}

// Chart data (frontend/src/charts/) -- shaped to feed directly into a
// Highcharts series config, field-for-field with backend/app/api/schemas.py's
// Chart*/PieChartData/VendorScorecardData models.
export interface ChartSeries {
  name: string;
  data: number[];
}

export interface ChartSeriesData {
  categories: string[];
  series: ChartSeries[];
}

export interface PieSlice {
  name: string;
  y: number;
}

export interface PieSeries {
  name: string;
  data: PieSlice[];
}

export interface PieChartData {
  series: PieSeries[];
}

export interface VendorScorecardEntry {
  vendor: string;
  sla_pct: number;
  cost_per_km: number;
  incident_count: number;
  sla_trend: number[];
}

export interface VendorScorecardData {
  vendors: VendorScorecardEntry[];
}

export interface ApiClient {
  getRoles(): Promise<Role[]>;
  getDashboard(persona: PersonaId): Promise<MetricCardData[]>;
  getNotifications(persona: PersonaId): Promise<NotificationItem[]>;
  resumeNotification(
    id: string,
    body: ResumeDecisionRequest,
  ): Promise<ResumeDecisionResponse>;
  getTrace(threadId: string): Promise<TraceStep[]>;
  getReports(persona: PersonaId): Promise<ReportMeta[]>;
  getChatHistory(persona: PersonaId): Promise<ChatMessage[]>;
  postChat(body: ChatRequest): Promise<ChatResponse>;
  getActivity(): Promise<ActivityEntry[]>;
  getOtaTrend(): Promise<ChartSeriesData>;
  getDelayReasons(): Promise<ChartSeriesData>;
  getNoShowTrend(): Promise<ChartSeriesData>;
  getAbsenceSplit(): Promise<PieChartData>;
  getBillingDiscrepancy(): Promise<ChartSeriesData>;
  getEmissionsByFuel(): Promise<ChartSeriesData>;
  getVendorScorecard(): Promise<VendorScorecardData>;
}
