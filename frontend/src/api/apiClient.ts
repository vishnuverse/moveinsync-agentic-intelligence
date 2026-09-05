import type {
  ActivityEntry,
  ApiClient,
  ChartSeriesData,
  ChatMessage,
  ChatRequest,
  ChatResponse,
  ChatThread,
  ChatThreadCreateRequest,
  ChatThreadRenameRequest,
  DataCoverage,
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
  ScopeOption,
  TraceStep,
  VendorScorecardData,
} from "./types";

const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "/api";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE_URL}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!res.ok) {
    // Surface the backend's own error detail (e.g. the message-length/empty
    // guardrail's 422, or the 502/503 "couldn't get a response" chat errors)
    // when the body is JSON with one, instead of only a generic status code
    // -- callers (ChatPanel's error state) show this text directly.
    let detail: string | undefined;
    try {
      const body = await res.clone().json();
      detail = typeof body?.detail === "string" ? body.detail : undefined;
    } catch {
      // response wasn't JSON -- fall through to the generic message below
    }
    throw new Error(detail ?? `API request failed: ${init?.method ?? "GET"} ${path} (${res.status})`);
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

export const realClient: ApiClient = {
  getRoles(): Promise<Role[]> {
    return request<Role[]>("/roles");
  },

  replayDemo(body: ReplayRequest): Promise<ReplayResponse> {
    return request<ReplayResponse>("/demo/replay", {
      method: "POST",
      body: JSON.stringify(body),
    });
  },

  getDashboard(persona: PersonaId): Promise<MetricCardData[]> {
    return request<MetricCardData[]>(`/dashboard?persona=${persona}`);
  },

  getNotifications(
    persona: PersonaId,
    opts?: PageOpts,
  ): Promise<Paginated<NotificationItem>> {
    const limit = opts?.limit ?? 25;
    const offset = opts?.offset ?? 0;
    return request<Paginated<NotificationItem>>(
      `/notifications?persona=${persona}&limit=${limit}&offset=${offset}`,
    );
  },

  resumeNotification(
    id: string,
    body: ResumeDecisionRequest,
  ): Promise<ResumeDecisionResponse> {
    return request<ResumeDecisionResponse>(`/notifications/${id}/resume`, {
      method: "POST",
      body: JSON.stringify(body),
    });
  },

  getTrace(threadId: string): Promise<TraceStep[]> {
    return request<TraceStep[]>(`/threads/${threadId}/trace`);
  },

  getReports(persona: PersonaId): Promise<ReportMeta[]> {
    return request<ReportMeta[]>(`/reports?persona=${persona}`);
  },

  generateReport(persona: PersonaId, report_type?: string): Promise<ReportMeta> {
    return request<ReportMeta>("/reports/generate", {
      method: "POST",
      body: JSON.stringify({ persona, report_type }),
    });
  },

  getDataCoverage(): Promise<DataCoverage> {
    return request<DataCoverage>("/data-coverage");
  },

  getChatThreads(persona: PersonaId): Promise<ChatThread[]> {
    return request<ChatThread[]>(`/chat/threads?persona=${persona}`);
  },

  createChatThread(body: ChatThreadCreateRequest): Promise<ChatThread> {
    return request<ChatThread>("/chat/threads", {
      method: "POST",
      body: JSON.stringify(body),
    });
  },

  renameChatThread(id: string, body: ChatThreadRenameRequest): Promise<ChatThread> {
    return request<ChatThread>(`/chat/threads/${id}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    });
  },

  deleteChatThread(id: string): Promise<void> {
    return request<void>(`/chat/threads/${id}`, { method: "DELETE" });
  },

  getThreadMessages(threadId: string): Promise<ChatMessage[]> {
    return request<ChatMessage[]>(`/chat/threads/${threadId}/messages`);
  },

  getScopeOptions(persona: PersonaId): Promise<ScopeOption[]> {
    return request<ScopeOption[]>(`/chat/scope-options?persona=${persona}`);
  },

  postChat(body: ChatRequest): Promise<ChatResponse> {
    return request<ChatResponse>("/chat", {
      method: "POST",
      body: JSON.stringify(body),
    });
  },

  getActivity(opts?: PageOpts): Promise<Paginated<ActivityEntry>> {
    const limit = opts?.limit ?? 25;
    const offset = opts?.offset ?? 0;
    return request<Paginated<ActivityEntry>>(
      `/activity?limit=${limit}&offset=${offset}`,
    );
  },

  getOtaTrend(days?: number): Promise<ChartSeriesData> {
    return request<ChartSeriesData>(`/charts/ota-trend${days ? `?days=${days}` : ""}`);
  },

  getDelayReasons(days?: number): Promise<ChartSeriesData> {
    return request<ChartSeriesData>(`/charts/delay-reasons${days ? `?days=${days}` : ""}`);
  },

  getNoShowTrend(days?: number): Promise<ChartSeriesData> {
    return request<ChartSeriesData>(`/charts/no-show-trend${days ? `?days=${days}` : ""}`);
  },

  getAbsenceSplit(days?: number): Promise<PieChartData> {
    return request<PieChartData>(`/charts/absence-split${days ? `?days=${days}` : ""}`);
  },

  getBillingDiscrepancy(months?: number): Promise<ChartSeriesData> {
    return request<ChartSeriesData>(`/charts/billing-discrepancy${months ? `?months=${months}` : ""}`);
  },

  getEmissionsByFuel(days?: number): Promise<ChartSeriesData> {
    return request<ChartSeriesData>(`/charts/emissions-by-fuel${days ? `?days=${days}` : ""}`);
  },

  getVendorScorecard(days?: number): Promise<VendorScorecardData> {
    return request<VendorScorecardData>(`/charts/vendor-scorecard${days ? `?days=${days}` : ""}`);
  },
};
