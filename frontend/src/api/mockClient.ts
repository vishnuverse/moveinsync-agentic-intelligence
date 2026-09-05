import {
  ACTIVITY_LOG,
  ROLES,
  TRACE_LIBRARY,
  absenceSplitMock,
  billingDiscrepancyMock,
  buildTrace,
  dashboardFor,
  delayReasonsMock,
  emissionsByFuelMock,
  initialChatHistory,
  initialNotifications,
  noShowTrendMock,
  otaTrendMock,
  reportsFor,
  scopeOptionsFor,
  vendorScorecardMock,
} from "./mockData";
import type {
  ActivityEntry,
  AggregatedInsightsResponse,
  ApiClient,
  ChartSeriesData,
  ChatMessage,
  ChatRequest,
  ChatResponse,
  ChatThread,
  ChatThreadCreateRequest,
  ChatThreadRenameRequest,
  CostOptimizationResponse,
  DataCoverage,
  MarkFalsePositiveRequest,
  MetricCardData,
  NotificationItem,
  PageOpts,
  Paginated,
  PersonaId,
  PieChartData,
  ReplayRequest,
  ReplayResponse,
  ReportMeta,
  ResumeDecisionRequest,
  ResumeDecisionResponse,
  Role,
  RulesResponse,
  RulesUpdateRequest,
  ScopeOption,
  TraceStep,
  UsageStatsResponse,
  VendorScorecardData,
} from "./types";
import { CHAT_MESSAGE_MAX_LEN } from "./chatLimits";

function delay(min = 220, max = 620): Promise<void> {
  const ms = min + Math.random() * (max - min);
  return new Promise((resolve) => setTimeout(resolve, ms));
}

const notificationStore: Record<PersonaId, NotificationItem[]> = {
  transport_manager: initialNotifications("transport_manager"),
  line_manager: initialNotifications("line_manager"),
  transport_head: initialNotifications("transport_head"),
};

const traceStore: Record<string, TraceStep[]> = { ...TRACE_LIBRARY };

// SP-B Settings page fixtures. "incident"/"escort_compliance_violation" are
// the two Major Risk Hotspot signal types (plan §B0) -- gate_mode/
// notification_cadence stay "force_escalate"/"immediate" here to mirror the
// backend's non-overridable safety floor; SettingsPage renders their
// controls disabled regardless of what this fixture says, but keeping the
// fixture consistent avoids a misleading mock-only state.
const rulesStore: RulesResponse = {
  signal_rules: [
    { signal_type: "incident", params: {}, gate_mode: "force_escalate", notification_cadence: "immediate", updated_at: new Date().toISOString() },
    { signal_type: "escort_compliance_violation", params: { violation_limit: 50, night_window_start_hour: 21, night_window_end_hour: 6, drop_delay_critical_minutes: 15 }, gate_mode: "force_escalate", notification_cadence: "immediate", updated_at: new Date().toISOString() },
    { signal_type: "delay_breach", params: { delay_threshold_minutes: 15 }, gate_mode: "auto", notification_cadence: "immediate", updated_at: new Date().toISOString() },
    { signal_type: "cost_divergence", params: { divergence_pct: 0.2 }, gate_mode: "auto", notification_cadence: "hourly", updated_at: new Date().toISOString() },
    { signal_type: "emissions_over_target", params: { min_ratio_over_baseline: 1.05 }, gate_mode: "auto", notification_cadence: "daily", updated_at: new Date().toISOString() },
    { signal_type: "attendance_correlated_with_transport", params: { delay_threshold_minutes: 15, min_late_samples: 3, signal_limit: 25, transport_correlation_ratio: 0.6, unrelated_correlation_ratio: 0.15 }, gate_mode: "auto", notification_cadence: "immediate", updated_at: new Date().toISOString() },
    { signal_type: "attendance_unrelated_late", params: {}, gate_mode: "auto", notification_cadence: "daily", updated_at: new Date().toISOString() },
    { signal_type: "billing_discrepancy", params: { min_slab_sample: 20, min_discrepancy_inr: 500 }, gate_mode: "auto", notification_cadence: "weekly", updated_at: new Date().toISOString() },
    { signal_type: "performance_variability", params: { cv_threshold_pct: 20, min_sample_size: 15, variability_minutes_floor: 10 }, gate_mode: "auto", notification_cadence: "daily", updated_at: new Date().toISOString() },
  ],
  gate_settings: {
    recurrence_window_hours: 24,
    recurrence_suppress_after: 3,
    max_consecutive_suppressions: 5,
    rule_only_margin_ratio: 2.0,
    max_fp_rate_for_rule_only: 0.2,
    min_confidence_for_rule_only: 0.6,
    max_healthy_suppression_rate: 0.8,
    escalation_after_hours_critical: 1.0,
    escalation_after_hours_high: 4.0,
    escalation_after_hours_medium: 24.0,
    updated_at: new Date().toISOString(),
  },
};

// Mutable per-persona report store seeded from the static mock, so a mocked
// "Generate report" can prepend a fresh meta and the subsequent list refresh
// actually shows it (mirrors the real POST /api/reports/generate persisting a
// new row that GET /api/reports then returns).
const reportStore: Record<PersonaId, ReportMeta[]> = {
  transport_manager: reportsFor("transport_manager"),
  line_manager: reportsFor("line_manager"),
  transport_head: reportsFor("transport_head"),
};

const REPORT_TITLE: Record<PersonaId, string> = {
  transport_manager: "Daily Ops Digest",
  line_manager: "Weekly Team Commute Digest",
  transport_head: "Leadership Report — Cost, Safety, Vendor & Carbon",
};

// --- Chat threads (mock mirror of chat_threads + episodic-memory-by-thread
// on the real backend) -- one thread per conversation, messages keyed by
// thread id so switching threads can never bleed history across them, same
// isolation guarantee app/api/chat.py's module docstring describes for the
// real store. Each persona starts with one seeded thread (the old
// persona-wide greeting) so the mock UI isn't empty on first load.
let threadCounter = 0;

function newThreadId(persona: PersonaId): string {
  threadCounter += 1;
  return `${persona}:chat:mock-${threadCounter}`;
}

function defaultTitle(firstMessage?: string): string {
  if (firstMessage && firstMessage.trim()) {
    const flat = firstMessage.trim().replace(/\s+/g, " ");
    return flat.length > 40 ? `${flat.slice(0, 40).trimEnd()}…` : flat;
  }
  return "New conversation";
}

const threadStore: Record<PersonaId, ChatThread[]> = {
  transport_manager: [],
  line_manager: [],
  transport_head: [],
};
const messageStore: Record<string, ChatMessage[]> = {};

function seedDefaultThread(persona: PersonaId): void {
  const id = newThreadId(persona);
  const now = new Date().toISOString();
  const thread: ChatThread = {
    id,
    persona,
    title: "Welcome",
    scope_entity_type: null,
    scope_entity_id: null,
    created_at: now,
    updated_at: now,
  };
  threadStore[persona] = [thread];
  messageStore[id] = initialChatHistory(persona).map((m) => ({ ...m, thread_id: id }));
}
(Object.keys(threadStore) as PersonaId[]).forEach(seedDefaultThread);

function findThread(threadId: string): ChatThread | undefined {
  for (const persona of Object.keys(threadStore) as PersonaId[]) {
    const found = threadStore[persona].find((t) => t.id === threadId);
    if (found) return found;
  }
  return undefined;
}

function touchThread(threadId: string): void {
  for (const persona of Object.keys(threadStore) as PersonaId[]) {
    const idx = threadStore[persona].findIndex((t) => t.id === threadId);
    if (idx !== -1) {
      threadStore[persona][idx] = { ...threadStore[persona][idx], updated_at: new Date().toISOString() };
      return;
    }
  }
}

const CHAT_TOPICS: Array<{
  keywords: string[];
  category: Parameters<typeof buildTrace>[0];
  answer: (persona: PersonaId) => string;
}> = [
  {
    keywords: ["late", "delay", "on-time", "on time", "sla"],
    category: "ontime",
    answer: (p) =>
      p === "line_manager"
        ? "Your team's on-time arrival is 93.5% this week, below the 95% target. The dip traces mainly to Route 14, where a driver shift-change overlap is adding 8-12 minutes most mornings — I've already flagged the correlated attendance records so no one gets an unfair late mark."
        : "On-time arrival is 91-92% right now, below the 95% SLA target. Route 14 is the main driver — 3 straight days of breaches tied to a driver shift-change overlap window. I'd recommend a short-term reallocation to a second vendor on that route while the schedule gets fixed.",
  },
  {
    keywords: ["cost", "expensive", "budget", "spend", "₹", "rupee"],
    category: "cost",
    answer: (p) =>
      p === "transport_head"
        ? "Fleet cost is ₹15.40 per passenger-km, comfortably inside the ₹12-18 industry band and flat for two quarters. Metro Cabs is the one vendor trending up (fuel surcharge), worth a rate conversation before it drifts toward the ceiling."
        : "Cost per passenger-km is ₹16.80, up from ₹14.20 last month but still inside the ₹12-18 industry band. The increase traces to a fuel surcharge from the Metro Cabs vendor rather than a route or scheduling issue.",
  },
  {
    keywords: ["safety", "incident", "accident", "crash", "brake"],
    category: "safety",
    answer: () =>
      "There have been 2 safety incidents in the last 7 days, both on Route 14 — one harsh-braking event (driver coaching filed) and one minor collision still under review. Both are held at the interrupt gate: nothing gets sent to the driver or vendor until a manager signs off.",
  },
  {
    keywords: ["vendor", "metro cabs", "quickride", "contract"],
    category: "vendorsla",
    answer: () =>
      "Metro Cabs has fallen to 88% SLA against a 95% contract, and below the 92% actionable-breach floor for 2 consecutive months. A contract review escalation is already drafted and waiting on your sign-off before it goes to procurement.",
  },
  {
    keywords: ["carbon", "emission", "co2", "sustainab", "green"],
    category: "carbon",
    answer: () =>
      "Fleet emissions are 74 gCO2 per passenger-km, about 9.8% below the 82 gCO2/pkm ICE-fleet baseline, driven mainly by the EV pilot on 3 routes. That puts us on track against this quarter's sustainability target.",
  },
  {
    keywords: ["attendance", "clock-in", "clock in", "team"],
    category: "attendance",
    answer: () =>
      "5 late clock-ins this week trace directly to shuttle delays on Route 14, not personal lateness. I've drafted a correction note for HR so those marks don't unfairly stick — it just needs your sign-off.",
  },
];

function pickCategory(message: string): Parameters<typeof buildTrace>[0] {
  const lower = message.toLowerCase();
  for (const topic of CHAT_TOPICS) {
    if (topic.keywords.some((k) => lower.includes(k))) return topic.category;
  }
  return "general";
}

function pickAnswer(message: string, persona: PersonaId): string {
  const lower = message.toLowerCase();
  for (const topic of CHAT_TOPICS) {
    if (topic.keywords.some((k) => lower.includes(k))) return topic.answer(persona);
  }
  return "Here's what the data shows: metrics across on-time performance, cost, safety, and vendor SLA are all within expected ranges except for the items already flagged in your notifications. Ask me about a specific route, vendor, or metric and I'll ground the answer in the live data with a visible trace.";
}

export const mockClient: ApiClient = {
  async getRoles(): Promise<Role[]> {
    await delay();
    return ROLES;
  },

  // Harmless stub: the live-day replay needs the real backend + a WS feed to
  // do anything meaningful, so in mock mode we just echo an empty summary
  // (the LiveEventFeed panel is itself inert in mock mode -- see liveEvents.ts).
  async replayDemo(body: ReplayRequest): Promise<ReplayResponse> {
    await delay(300, 700);
    return {
      scenario: body.scenario,
      org_id: body.org_id ?? "mock-org",
      injected_trip_ids: [],
      new_trip_ids: [],
      pipeline_summary: [],
    };
  },

  async getDashboard(persona: PersonaId): Promise<MetricCardData[]> {
    await delay();
    return dashboardFor(persona);
  },

  async getNotifications(
    persona: PersonaId,
    opts?: PageOpts,
  ): Promise<Paginated<NotificationItem>> {
    await delay();
    const all = notificationStore[persona];
    const limit = opts?.limit ?? 25;
    const offset = opts?.offset ?? 0;
    return { items: all.slice(offset, offset + limit), total: all.length };
  },

  async resumeNotification(
    id: string,
    { decision }: ResumeDecisionRequest,
  ): Promise<ResumeDecisionResponse> {
    await delay(300, 700);
    for (const persona of Object.keys(notificationStore) as PersonaId[]) {
      const list = notificationStore[persona];
      const idx = list.findIndex((n) => n.id === id);
      if (idx !== -1) {
        const status: NotificationItem["status"] = "acked";
        list[idx] = { ...list[idx], status };
        const resolvedAt = new Date().toISOString();
        console.debug(`[mock] notification ${id} resolved via ${decision}`);
        return { id, status, resolved_at: resolvedAt };
      }
    }
    throw new Error(`Notification ${id} not found`);
  },

  async markFalsePositive(id: string, body?: MarkFalsePositiveRequest): Promise<ResumeDecisionResponse> {
    await delay(300, 700);
    for (const persona of Object.keys(notificationStore) as PersonaId[]) {
      const list = notificationStore[persona];
      const idx = list.findIndex((n) => n.id === id);
      if (idx !== -1) {
        list[idx] = { ...list[idx], status: "acked", is_false_positive: true };
        console.debug(`[mock] notification ${id} marked false positive: ${body?.note ?? "(no note)"}`);
        return { id, status: "acked", resolved_at: new Date().toISOString() };
      }
    }
    throw new Error(`Notification ${id} not found`);
  },

  async getTrace(threadId: string): Promise<TraceStep[]> {
    await delay();
    if (traceStore[threadId]) return traceStore[threadId];
    const generated = buildTrace("general", 3);
    traceStore[threadId] = generated;
    return generated;
  },

  async getReports(persona: PersonaId): Promise<ReportMeta[]> {
    await delay();
    return [...reportStore[persona]];
  },

  // Fake generator: waits a couple of seconds (so the button's "Generating…"
  // state is visible) then prepends a fresh meta the list refresh will show.
  async generateReport(persona: PersonaId, reportType?: string): Promise<ReportMeta> {
    await delay(1600, 2600);
    const now = new Date();
    const label = reportType ? `${REPORT_TITLE[persona]} (${reportType})` : REPORT_TITLE[persona];
    const stamp = now.toISOString().slice(0, 10);
    const report: ReportMeta = {
      id: `${persona}-gen-${now.getTime()}`,
      title: `${label} — ${stamp}`,
      period: stamp,
      generated_at: now.toISOString(),
      preview_url: `/reports/${persona}-gen-${now.getTime()}.html`,
    };
    reportStore[persona] = [report, ...reportStore[persona]];
    return report;
  },

  async getDataCoverage(): Promise<DataCoverage> {
    await delay(120, 280);
    return { start_date: "2026-05-01", end_date: "2026-07-31", trip_count: 4200, dense_end_date: "2026-07-31" };
  },

  async getChatThreads(persona: PersonaId): Promise<ChatThread[]> {
    await delay();
    return [...threadStore[persona]].sort((a, b) => b.updated_at.localeCompare(a.updated_at));
  },

  async createChatThread(body: ChatThreadCreateRequest): Promise<ChatThread> {
    await delay(150, 350);
    const now = new Date().toISOString();
    const thread: ChatThread = {
      id: newThreadId(body.persona),
      persona: body.persona,
      title: "New conversation",
      scope_entity_type: body.scope_entity_type ?? null,
      scope_entity_id: body.scope_entity_id ?? null,
      created_at: now,
      updated_at: now,
    };
    threadStore[body.persona] = [thread, ...threadStore[body.persona]];
    messageStore[thread.id] = [];
    return thread;
  },

  async renameChatThread(id: string, body: ChatThreadRenameRequest): Promise<ChatThread> {
    await delay(120, 280);
    const title = body.title.trim();
    if (!title) throw new Error("title must not be empty");
    for (const persona of Object.keys(threadStore) as PersonaId[]) {
      const idx = threadStore[persona].findIndex((t) => t.id === id);
      if (idx !== -1) {
        const updated: ChatThread = { ...threadStore[persona][idx], title, updated_at: new Date().toISOString() };
        threadStore[persona][idx] = updated;
        return updated;
      }
    }
    throw new Error(`chat thread ${id} not found`);
  },

  async deleteChatThread(id: string): Promise<void> {
    await delay(120, 280);
    for (const persona of Object.keys(threadStore) as PersonaId[]) {
      threadStore[persona] = threadStore[persona].filter((t) => t.id !== id);
    }
    delete messageStore[id];
  },

  async getThreadMessages(threadId: string): Promise<ChatMessage[]> {
    await delay();
    return [...(messageStore[threadId] ?? [])];
  },

  async getScopeOptions(persona: PersonaId): Promise<ScopeOption[]> {
    await delay(120, 280);
    return scopeOptionsFor(persona);
  },

  async postChat(body: ChatRequest): Promise<ChatResponse> {
    const trimmed = body.message.trim();
    if (!trimmed) throw new Error("message must not be empty");
    if (trimmed.length > CHAT_MESSAGE_MAX_LEN) {
      throw new Error(`message must be ${CHAT_MESSAGE_MAX_LEN} characters or fewer`);
    }
    await delay(400, 900);
    const { persona, message } = body;

    let threadId = body.thread_id;
    if (!threadId || !messageStore[threadId]) {
      const now = new Date().toISOString();
      const thread: ChatThread = {
        id: newThreadId(persona),
        persona,
        title: defaultTitle(message),
        scope_entity_type: null,
        scope_entity_id: null,
        created_at: now,
        updated_at: now,
      };
      threadStore[persona] = [thread, ...threadStore[persona]];
      messageStore[thread.id] = [];
      threadId = thread.id;
    }

    const userMsg: ChatMessage = {
      id: `${persona}-u-${Date.now()}`,
      role: "user",
      text: message,
      thread_id: threadId,
      created_at: new Date().toISOString(),
    };
    const category = pickCategory(message);
    traceStore[threadId] = buildTrace(category, 1);
    // Mirrors app/api/chat.py's _compose_question: a scoped thread biases the
    // canned answer toward the picked entity, same prompt-composition idea
    // as the real backend applies to the NL question sent to the SQL agent.
    const thread = findThread(threadId);
    const baseAnswer = pickAnswer(message, persona);
    const answerText =
      thread?.scope_entity_type && thread?.scope_entity_id
        ? `Regarding ${thread.scope_entity_type} '${thread.scope_entity_id}': ${baseAnswer}`
        : baseAnswer;
    const agentMsg: ChatMessage = {
      id: `${persona}-a-${Date.now()}`,
      role: "agent",
      text: answerText,
      thread_id: threadId,
      created_at: new Date().toISOString(),
    };
    messageStore[threadId] = [...(messageStore[threadId] ?? []), userMsg, agentMsg];
    touchThread(threadId);
    return { message: agentMsg };
  },

  async getActivity(opts?: PageOpts): Promise<Paginated<ActivityEntry>> {
    await delay();
    const limit = opts?.limit ?? 25;
    const offset = opts?.offset ?? 0;
    return { items: ACTIVITY_LOG.slice(offset, offset + limit), total: ACTIVITY_LOG.length };
  },

  // `range` is accepted for interface parity with the real client (the
  // date-range picker passes it), but the mock's fixtures are synthetic
  // random series keyed only by day-count -- an explicit since/until has
  // nothing real to slice, so it's a no-op here.
  async getOtaTrend(days = 45): Promise<ChartSeriesData> {
    await delay();
    return otaTrendMock(days);
  },

  async getDelayReasons(days = 90): Promise<ChartSeriesData> {
    await delay();
    return delayReasonsMock(days);
  },

  async getNoShowTrend(days = 45): Promise<ChartSeriesData> {
    await delay();
    return noShowTrendMock(days);
  },

  async getAbsenceSplit(): Promise<PieChartData> {
    await delay();
    return absenceSplitMock();
  },

  async getBillingDiscrepancy(months = 6): Promise<ChartSeriesData> {
    await delay();
    return billingDiscrepancyMock(months);
  },

  async getEmissionsByFuel(days = 90): Promise<ChartSeriesData> {
    await delay();
    return emissionsByFuelMock(days);
  },

  async getVendorScorecard(days = 90): Promise<VendorScorecardData> {
    await delay();
    return vendorScorecardMock(days);
  },

  async getSignalGateFunnel(): Promise<ChartSeriesData> {
    await delay();
    const categories = Array.from({ length: 14 }, (_, i) => {
      const d = new Date();
      d.setDate(d.getDate() - (13 - i));
      return d.toISOString().slice(0, 10);
    });
    return {
      categories,
      series: [
        { name: "Suppressed", data: categories.map(() => Math.round(8 + Math.random() * 10)) },
        { name: "Rule-Only", data: categories.map(() => Math.round(3 + Math.random() * 6)) },
        { name: "Escalated", data: categories.map(() => Math.round(1 + Math.random() * 4)) },
      ],
    };
  },

  async getLlmUsage(): Promise<ChartSeriesData> {
    await delay();
    const categories = Array.from({ length: 14 }, (_, i) => {
      const d = new Date();
      d.setDate(d.getDate() - (13 - i));
      return d.toISOString().slice(0, 10);
    });
    return {
      categories,
      series: [{ name: "LLM calls", data: categories.map(() => Math.round(20 + Math.random() * 60)) }],
      breach_threshold: 500,
      target_label: "Daily budget",
    };
  },

  async getRules(): Promise<RulesResponse> {
    await delay();
    return structuredClone(rulesStore);
  },

  async updateRules(body: RulesUpdateRequest): Promise<RulesResponse> {
    await delay(300, 700);
    if (body.signal_rules) {
      for (const rule of body.signal_rules) {
        const idx = rulesStore.signal_rules.findIndex((r) => r.signal_type === rule.signal_type);
        const updated = { ...rule, updated_at: new Date().toISOString() };
        if (idx !== -1) rulesStore.signal_rules[idx] = updated;
        else rulesStore.signal_rules.push(updated);
      }
    }
    if (body.gate_settings) {
      rulesStore.gate_settings = { ...body.gate_settings, updated_at: new Date().toISOString() };
    }
    return structuredClone(rulesStore);
  },

  async getUsageStats(): Promise<UsageStatsResponse> {
    await delay();
    return {
      llm_calls_today: 132,
      llm_daily_limit: 500,
      gate_counts_today: { suppress: 14, rule_only: 6, escalate: 9 },
      false_positive_rate_by_signal_type: [
        { signal_type: "delay_breach", dispatched_count: 40, false_positive_count: 3, false_positive_rate_pct: 7.5 },
        { signal_type: "cost_divergence", dispatched_count: 12, false_positive_count: 0, false_positive_rate_pct: 0 },
      ],
      suppression_warnings: [],
    };
  },

  async getPersonaInsights(persona: PersonaId): Promise<AggregatedInsightsResponse> {
    await delay();
    if (persona === "transport_manager") {
      return {
        no_shows_today: 12,
        no_shows_this_week: 68,
        no_shows_trend_pct: 8.4,
        no_shows_trend_direction: "up",
        flagged_driver_count: 3,
        total_drivers_evaluated: 45,
      };
    }
    if (persona === "line_manager") {
      return {
        no_shows_today: 2,
        no_shows_this_week: 9,
        no_shows_trend_pct: -12.0,
        no_shows_trend_direction: "down",
      };
    }
    return {
      no_shows_today: 18,
      no_shows_this_week: 96,
      no_shows_trend_pct: 3.1,
      no_shows_trend_direction: "flat",
    };
  },

  async getCostOptimization(since?: string, until?: string): Promise<CostOptimizationResponse> {
    await delay();
    const end = until ?? new Date().toISOString().slice(0, 10);
    const start = since ?? end;
    return {
      window_start: start,
      window_end: end,
      window_total_inr: 3_842_500,
      baseline_avg_per_day_inr: 3_500_000,
      trend_pct: 9.8,
      trend_direction: "up",
      opportunities: [
        {
          vendor_name: "Aarav Mikhailov Travel",
          cv_pct: 118.1,
          recommendation:
            "Aarav Mikhailov Travel's per-km billing is 118% inconsistent in this window -- an invoice audit or rate-card renegotiation is worth prioritizing here.",
        },
        {
          vendor_name: "Divya Mikhailov Travel",
          cv_pct: 94.6,
          recommendation:
            "Divya Mikhailov Travel's per-km billing is 95% inconsistent in this window -- worth a closer look at contract terms.",
        },
      ],
    };
  },
};
