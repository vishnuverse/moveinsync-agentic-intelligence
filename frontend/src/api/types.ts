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
  // Omitted only for the very first message of a brand-new conversation
  // started through the legacy/implicit-create path -- the primary flow is
  // "create a thread, then post into it" (see ChatPage/ChatThreadList).
  thread_id?: string;
}

export interface ChatResponse {
  message: ChatMessage;
}

// Chat history feature: threads + the "select something to chat with" scope
// picker (backend/app/api/chat.py, backend/app/services/{chat_threads,
// scope_options}.py). `id` doubles as the LangGraph checkpoint thread_id, so
// it's what TraceDrawer/getTrace already expects.
export interface ChatThread {
  id: string;
  persona: PersonaId;
  title: string;
  scope_entity_type?: string | null;
  scope_entity_id?: string | null;
  created_at: string;
  updated_at: string;
}

export interface ChatThreadCreateRequest {
  persona: PersonaId;
  scope_entity_type?: string;
  scope_entity_id?: string;
}

export interface ChatThreadRenameRequest {
  title: string;
}

// `id` is a human/DB-grounded value (a vendor name, a route_code, a team
// name, a region), not a numeric key -- see scope_options.py's docstring for
// why: it gets sent back verbatim as scope_entity_id and prepended straight
// into the NL question.
export interface ScopeOption {
  type: string;
  id: string;
  label: string;
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

// vs-previous-window delta -- a second cheap SQL aggregate over the prior
// equal-length window (see backend/app/services/chart_data.py), not an LLM
// call, so charts can show "context, not just a number" at no extra cost.
export interface ChartComparison {
  label: string;
  current_value: number;
  previous_value: number;
  delta_pct: number;
}

export interface ChartContributor {
  name: string;
  value: number;
  pct: number;
}

export interface ChartSeriesData {
  categories: string[];
  series: ChartSeries[];
  /** Benchmark reference value, sourced server-side from the seeded
   * `sustainability_targets` table -- never a magic number in this file. */
  target?: number;
  breach_threshold?: number;
  target_label?: string;
  comparison?: ChartComparison;
  /** Billing-discrepancy only: top vendors responsible for the gap. */
  contributors?: ChartContributor[];
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
  ontime_pct_current?: number;
  ontime_pct_prev?: number;
}

export interface VendorScorecardData {
  vendors: VendorScorecardEntry[];
}

// --- "Simulate live day" demo (SP-A): POST /api/demo/replay injects real
// re-timestamped rows and runs the pipeline inline so the WS feed lights up.
export type DemoScenario =
  | "delay_spike"
  | "escort_violation"
  | "billing_discrepancy"
  | "emissions_over_target";

export interface ReplayRequest {
  scenario: DemoScenario;
  count?: number;
  org_id?: string;
}

export interface ReplayResponse {
  scenario: string;
  org_id: string;
  injected_trip_ids: number[];
  new_trip_ids: number[];
  pipeline_summary: Array<Record<string, unknown>>;
}

// A single frame received over the `/api/ws/{persona}` WebSocket. Mirrors the
// payload shapes app/graph/act/nodes.py publishes to `notifications:{persona}`
// (kinds: notification | needs_intervention | dispatched | rejected), plus the
// `published_at` stamp redis_publish.py adds. All fields but `kind` are
// optional because the four kinds carry different subsets.
export type LiveEventKind =
  | "notification"
  | "needs_intervention"
  | "dispatched"
  | "rejected";

export interface LiveEvent {
  kind: LiveEventKind;
  notification_id?: string | number;
  status?: string;
  severity?: NotificationSeverity;
  title?: string;
  persona?: PersonaId | string;
  thread_id?: string;
  action_type?: string;
  summary?: string | null;
  recommendation?: string | null;
  published_at?: string;
}

// --- Pagination (SP: Notifications + Agent Activity). The list endpoints now
// return an envelope so the UI can offer a "Load more" that walks offsets
// until items.length >= total, instead of assuming it got everything at once.
export interface PageOpts {
  limit?: number;
  offset?: number;
}

export interface Paginated<T> {
  items: T[];
  total: number;
}

// GET /api/data-coverage -- the date span + trip count the live/demo data
// actually covers, so the Live page can label its window instead of implying
// "now". Dates are "YYYY-MM-DD"; null when the dataset is empty.
export interface DataCoverage {
  start_date: string | null;
  end_date: string | null;
  trip_count: number;
}

export interface ApiClient {
  getRoles(): Promise<Role[]>;
  replayDemo(body: ReplayRequest): Promise<ReplayResponse>;
  getDashboard(persona: PersonaId): Promise<MetricCardData[]>;
  getNotifications(
    persona: PersonaId,
    opts?: PageOpts,
  ): Promise<Paginated<NotificationItem>>;
  resumeNotification(
    id: string,
    body: ResumeDecisionRequest,
  ): Promise<ResumeDecisionResponse>;
  getTrace(threadId: string): Promise<TraceStep[]>;
  getReports(persona: PersonaId): Promise<ReportMeta[]>;
  generateReport(persona: PersonaId, report_type?: string): Promise<ReportMeta>;
  getDataCoverage(): Promise<DataCoverage>;
  getChatThreads(persona: PersonaId): Promise<ChatThread[]>;
  createChatThread(body: ChatThreadCreateRequest): Promise<ChatThread>;
  renameChatThread(id: string, body: ChatThreadRenameRequest): Promise<ChatThread>;
  deleteChatThread(id: string): Promise<void>;
  getThreadMessages(threadId: string): Promise<ChatMessage[]>;
  getScopeOptions(persona: PersonaId): Promise<ScopeOption[]>;
  postChat(body: ChatRequest): Promise<ChatResponse>;
  getActivity(opts?: PageOpts): Promise<Paginated<ActivityEntry>>;
  getOtaTrend(days?: number): Promise<ChartSeriesData>;
  getDelayReasons(days?: number): Promise<ChartSeriesData>;
  getNoShowTrend(days?: number): Promise<ChartSeriesData>;
  getAbsenceSplit(days?: number): Promise<PieChartData>;
  getBillingDiscrepancy(months?: number): Promise<ChartSeriesData>;
  getEmissionsByFuel(days?: number): Promise<ChartSeriesData>;
  getVendorScorecard(days?: number): Promise<VendorScorecardData>;
}
