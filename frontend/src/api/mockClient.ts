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
  vendorScorecardMock,
} from "./mockData";
import type {
  ActivityEntry,
  ApiClient,
  ChartSeriesData,
  ChatMessage,
  ChatRequest,
  ChatResponse,
  MetricCardData,
  NotificationItem,
  PersonaId,
  PieChartData,
  ReportMeta,
  ResumeDecisionRequest,
  ResumeDecisionResponse,
  Role,
  TraceStep,
  VendorScorecardData,
} from "./types";

function delay(min = 220, max = 620): Promise<void> {
  const ms = min + Math.random() * (max - min);
  return new Promise((resolve) => setTimeout(resolve, ms));
}

const notificationStore: Record<PersonaId, NotificationItem[]> = {
  transport_manager: initialNotifications("transport_manager"),
  line_manager: initialNotifications("line_manager"),
  transport_head: initialNotifications("transport_head"),
};

const chatStore: Record<PersonaId, ChatMessage[]> = {
  transport_manager: initialChatHistory("transport_manager"),
  line_manager: initialChatHistory("line_manager"),
  transport_head: initialChatHistory("transport_head"),
};

const traceStore: Record<string, TraceStep[]> = { ...TRACE_LIBRARY };

let chatThreadCounter = 0;

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

  async getDashboard(persona: PersonaId): Promise<MetricCardData[]> {
    await delay();
    return dashboardFor(persona);
  },

  async getNotifications(persona: PersonaId): Promise<NotificationItem[]> {
    await delay();
    return [...notificationStore[persona]];
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

  async getTrace(threadId: string): Promise<TraceStep[]> {
    await delay();
    if (traceStore[threadId]) return traceStore[threadId];
    const generated = buildTrace("general", 3);
    traceStore[threadId] = generated;
    return generated;
  },

  async getReports(persona: PersonaId): Promise<ReportMeta[]> {
    await delay();
    return reportsFor(persona);
  },

  async getChatHistory(persona: PersonaId): Promise<ChatMessage[]> {
    await delay();
    return [...chatStore[persona]];
  },

  async postChat(body: ChatRequest): Promise<ChatResponse> {
    await delay(400, 900);
    const { persona, message } = body;
    const userMsg: ChatMessage = {
      id: `${persona}-u-${Date.now()}`,
      role: "user",
      text: message,
      created_at: new Date().toISOString(),
    };
    chatThreadCounter += 1;
    const threadId = `chat-${persona}-${chatThreadCounter}`;
    const category = pickCategory(message);
    traceStore[threadId] = buildTrace(category, 1);
    const agentMsg: ChatMessage = {
      id: `${persona}-a-${Date.now()}`,
      role: "agent",
      text: pickAnswer(message, persona),
      thread_id: threadId,
      created_at: new Date().toISOString(),
    };
    chatStore[persona] = [...chatStore[persona], userMsg, agentMsg];
    return { message: agentMsg };
  },

  async getActivity(): Promise<ActivityEntry[]> {
    await delay();
    return ACTIVITY_LOG;
  },

  async getOtaTrend(): Promise<ChartSeriesData> {
    await delay();
    return otaTrendMock();
  },

  async getDelayReasons(): Promise<ChartSeriesData> {
    await delay();
    return delayReasonsMock();
  },

  async getNoShowTrend(): Promise<ChartSeriesData> {
    await delay();
    return noShowTrendMock();
  },

  async getAbsenceSplit(): Promise<PieChartData> {
    await delay();
    return absenceSplitMock();
  },

  async getBillingDiscrepancy(): Promise<ChartSeriesData> {
    await delay();
    return billingDiscrepancyMock();
  },

  async getEmissionsByFuel(): Promise<ChartSeriesData> {
    await delay();
    return emissionsByFuelMock();
  },

  async getVendorScorecard(): Promise<VendorScorecardData> {
    await delay();
    return vendorScorecardMock();
  },
};
