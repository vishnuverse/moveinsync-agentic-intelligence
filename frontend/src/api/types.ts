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
  /** Defaults to false server-side (Pydantic default); optional here so
   * every pre-existing mock fixture doesn't need updating individually. */
  is_false_positive?: boolean;
}

export interface MarkFalsePositiveRequest {
  note?: string;
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
  | "gate_decision"
  | "sql_generated"
  | "sql_executed"
  | "context_built"
  | "decision"
  | "escalation";

export interface TraceStep {
  step: TraceStepType;
  label: string;
  detail: string;
  sql?: string;
  retry_count?: number;
  timestamp: string;
  /** Populated only on the "decision" step, when the decision carries a
   * recommendation -- rendered as a distinct "Recommended Action" block
   * instead of being re-parsed out of `detail`. */
  recommendation?: string;
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
  /** One-line plain-language framing of the window, when the chart has one. */
  summary?: string;
}

export interface PieSlice {
  name: string;
  y: number;
}

// GET /api/charts/hotspot-timeline -- one row per real calendar day with
// Major Risk Hotspot activity (unescorted late-night female trips, critical/
// high-severity incidents), grounded in each event's own date, not when the
// demo pipeline happened to process it. Powers the dashboard's colored,
// click/drag-selectable hotspot timeline.
export interface HotspotDay {
  date: string;
  escort_violations: number;
  critical_incidents: number;
  high_incidents: number;
}

export interface HotspotTimelineResponse {
  days: HotspotDay[];
  window_since: string;
  window_until: string;
}

// GET /api/charts/signal-timeline?persona=X -- Line Manager/Transport
// Head's own analog of the hotspot timeline (Transport Manager keeps the
// richer HotspotDay shape above). Same generic 2-field shape for both
// personas; what primary/marker actually mean is persona-specific (see
// backend/app/services/chart_data.py::signal_timeline's docstring).
export interface TimelineDay {
  date: string;
  primary_count: number;
  marker_count: number;
}

export interface SignalTimelineResponse {
  days: TimelineDay[];
  window_since: string | null;
  window_until: string | null;
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
  // Most recent date with substantial trip volume -- distinct from
  // `end_date` (the literal max, which can be a sparse live-replay tail
  // day). The date-range picker defaults its window to end here, not at
  // `end_date`, so first-load always shows meaningful data; `start_date`/
  // `end_date` remain its outer slider bounds.
  dense_end_date: string | null;
}

// A user-picked (or default) window for the sliding date-range picker --
// "YYYY-MM-DD" strings, both ends inclusive. Passed through to every
// chart/insight endpoint that accepts since/until overrides.
export interface DateRange {
  since: string;
  until: string;
}

// --- SP-B Settings page: per-signal-type thresholds/gate-mode/cadence,
// global gate policy, and an at-a-glance usage/health snapshot.
export type GateMode = "auto" | "force_suppress" | "force_rule_only" | "force_escalate";
export type NotificationCadence = "immediate" | "hourly" | "every_2_hours" | "daily" | "weekly";

export interface SignalRuleParams {
  signal_type: string;
  params: Record<string, number | string>;
  gate_mode: GateMode;
  notification_cadence: NotificationCadence;
  updated_at: string;
  updated_by?: string | null;
}

export interface GateSettings {
  recurrence_window_hours: number;
  recurrence_suppress_after: number;
  max_consecutive_suppressions: number;
  rule_only_margin_ratio: number;
  max_fp_rate_for_rule_only: number;
  min_confidence_for_rule_only: number;
  max_healthy_suppression_rate: number;
  escalation_after_hours_critical: number;
  escalation_after_hours_high: number;
  escalation_after_hours_medium: number;
  updated_at: string;
  updated_by?: string | null;
}

export interface RulesResponse {
  signal_rules: SignalRuleParams[];
  gate_settings: GateSettings;
}

export interface RulesUpdateRequest {
  signal_rules?: SignalRuleParams[];
  gate_settings?: GateSettings;
  updated_by?: string;
}

export interface FalsePositiveRateEntry {
  signal_type: string;
  dispatched_count: number;
  false_positive_count: number;
  false_positive_rate_pct: number;
}

export interface UsageStatsResponse {
  llm_calls_today: number;
  llm_daily_limit: number;
  gate_counts_today: Record<string, number>;
  false_positive_rate_by_signal_type: FalsePositiveRateEntry[];
  suppression_warnings: string[];
}

// --- SP-B aggregated insights + cost-optimization-for-a-window
export interface AggregatedInsightsResponse {
  no_shows_today?: number | null;
  no_shows_this_week?: number | null;
  no_shows_trend_pct?: number | null;
  no_shows_trend_direction?: MetricTrend | null;
  flagged_driver_count?: number | null;
  total_drivers_evaluated?: number | null;
}

export interface CostOptimizationOpportunity {
  vendor_name: string;
  cv_pct: number;
  recommendation: string;
}

export interface CostOptimizationResponse {
  window_start: string;
  window_end: string;
  window_total_inr: number;
  baseline_avg_per_day_inr: number;
  trend_pct?: number | null;
  trend_direction: MetricTrend;
  opportunities: CostOptimizationOpportunity[];
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
  getOtaTrend(days?: number, range?: DateRange): Promise<ChartSeriesData>;
  getDelayReasons(days?: number, range?: DateRange): Promise<ChartSeriesData>;
  getNoShowTrend(days?: number, range?: DateRange): Promise<ChartSeriesData>;
  getAbsenceSplit(days?: number, range?: DateRange): Promise<PieChartData>;
  getBillingDiscrepancy(months?: number, range?: DateRange): Promise<ChartSeriesData>;
  getEmissionsByFuel(days?: number, range?: DateRange): Promise<ChartSeriesData>;
  getEscortCompliance(days?: number, range?: DateRange): Promise<ChartSeriesData>;
  getVendorScorecard(days?: number, range?: DateRange): Promise<VendorScorecardData>;
  getHotspotTimeline(days?: number, range?: DateRange): Promise<HotspotTimelineResponse>;
  getSignalTimeline(persona: PersonaId, days?: number, range?: DateRange): Promise<SignalTimelineResponse>;
  getSignalGateFunnel(days?: number): Promise<ChartSeriesData>;
  getLlmUsage(days?: number): Promise<ChartSeriesData>;
  getRules(): Promise<RulesResponse>;
  updateRules(body: RulesUpdateRequest): Promise<RulesResponse>;
  getUsageStats(): Promise<UsageStatsResponse>;
  markFalsePositive(id: string, body?: MarkFalsePositiveRequest): Promise<ResumeDecisionResponse>;
  getPersonaInsights(persona: PersonaId): Promise<AggregatedInsightsResponse>;
  getCostOptimization(since?: string, until?: string): Promise<CostOptimizationResponse>;
}
