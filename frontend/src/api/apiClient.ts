import type {
  ActivityEntry,
  ApiClient,
  ChatMessage,
  ChatRequest,
  ChatResponse,
  MetricCardData,
  NotificationItem,
  PersonaId,
  ReportMeta,
  ResumeDecisionRequest,
  ResumeDecisionResponse,
  Role,
  TraceStep,
} from "./types";

const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "/api";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE_URL}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!res.ok) {
    throw new Error(`API request failed: ${init?.method ?? "GET"} ${path} (${res.status})`);
  }
  return res.json() as Promise<T>;
}

export const realClient: ApiClient = {
  getRoles(): Promise<Role[]> {
    return request<Role[]>("/roles");
  },

  getDashboard(persona: PersonaId): Promise<MetricCardData[]> {
    return request<MetricCardData[]>(`/dashboard?persona=${persona}`);
  },

  getNotifications(persona: PersonaId): Promise<NotificationItem[]> {
    return request<NotificationItem[]>(`/notifications?persona=${persona}`);
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

  getChatHistory(persona: PersonaId): Promise<ChatMessage[]> {
    return request<ChatMessage[]>(`/chat/history?persona=${persona}`);
  },

  postChat(body: ChatRequest): Promise<ChatResponse> {
    return request<ChatResponse>("/chat", {
      method: "POST",
      body: JSON.stringify(body),
    });
  },

  getActivity(): Promise<ActivityEntry[]> {
    return request<ActivityEntry[]>("/activity");
  },
};
