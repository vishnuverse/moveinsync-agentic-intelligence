import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import type { ChatMessage, PersonaId } from "../api";
import { CHAT_MESSAGE_MAX_LEN } from "../api/chatLimits";
import { useAppState } from "../state/AppStateContext";
import { withTimeout } from "../lib/timeout";
import { EmptyState, ErrorState, LoadingState } from "./AsyncStatus";
import { IconSearch } from "./icons";
import "./ChatPanel.css";

function formatTime(ts: string): string {
  return new Date(ts).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

// withTimeout()'s 20s default (lib/timeout.ts) fits the cheap, LLM-free
// fetches it was built for (dashboard/notifications/charts) but is too
// tight here -- confirmed live: a real chat turn runs up to three sequential
// LLM calls (route -> SQL agent -> root-cause synthesis, see
// app/graph/reason/subgraph.py) against Sarvam, a non-frontier model chosen
// for cost, and took ~30s end-to-end while returning a normal 200. A 20s
// client timeout on that path doesn't fail safely, it fails FALSELY -- the
// backend still finishes the work (and still counts it against the daily
// LLM-call budget) while the UI tells the user it didn't. History-loading
// GETs on this same panel keep the shared default; only the LLM-driven send
// needs the longer budget.
const CHAT_SEND_TIMEOUT_MS = 60000;

// Real, answerable-against-the-live-schema questions per persona -- picked
// (and 2-3 verified live against the real backend, not the mock) so a new
// user isn't staring at a blank input with no idea what the agent can do.
const EXAMPLE_PROMPTS: Record<PersonaId, string[]> = {
  transport_manager: [
    "Which route had the worst on-time performance this week?",
    "What's driving the delays on our worst route right now?",
    "How does our cost per kilometer compare to the industry benchmark?",
    "Are there any safety incidents I should know about?",
    "Which vendor is falling short of their SLA?",
  ],
  line_manager: [
    "What's my team's no-show rate this month?",
    "Are shuttle delays affecting my team's attendance?",
    "Which of my team members have the most late arrivals?",
    "How does my team's on-time rate compare to last month?",
  ],
  transport_head: [
    "Which vendor has the highest billing discrepancy?",
    "How do our emissions compare to the industry baseline?",
    "What's our fleet cost efficiency versus target?",
    "Which vendors are below their contracted SLA?",
  ],
};

interface ChatPanelProps {
  // null = no thread selected/created yet (a brand-new persona with no
  // conversations, or the thread list still loading) -- rendered as a
  // distinct empty state, not an infinite loading spinner or a silent no-op.
  threadId: string | null;
  // Fired when POST /chat creates a thread implicitly (the caller had no
  // threadId yet) -- lets the parent (ChatPage) adopt it as the active
  // thread so the NEXT message lands in the same conversation instead of
  // creating a new one on every send.
  onThreadCreated: (threadId: string) => void;
  // Fired after every successful send so the thread list can refresh its
  // ordering/title (POST /chat bumps updated_at server-side, and a first
  // message sets the auto-generated title) without this panel needing to
  // know anything about how that list is rendered.
  onThreadActivity: () => void;
}

export function ChatPanel({ threadId, onThreadCreated, onThreadActivity }: ChatPanelProps) {
  const { persona, openTrace } = useAppState();
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [sendError, setSendError] = useState<string | null>(null);
  const [historyStatus, setHistoryStatus] = useState<"loading" | "ready" | "error" | "empty">(
    threadId ? "loading" : "empty",
  );
  const scrollRef = useRef<HTMLDivElement>(null);

  function loadHistory() {
    if (!threadId) {
      setHistoryStatus("empty");
      setMessages([]);
      return;
    }
    setHistoryStatus("loading");
    withTimeout(api.getThreadMessages(threadId))
      .then((res) => {
        setMessages(res);
        setHistoryStatus("ready");
      })
      .catch(() => setHistoryStatus("error"));
  }

  useEffect(() => {
    loadHistory();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [threadId]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages]);

  async function sendMessage(text: string) {
    const trimmed = text.trim();
    if (!trimmed || sending) return;
    if (trimmed.length > CHAT_MESSAGE_MAX_LEN) {
      // Belt-and-suspenders: the <input maxLength> below already blocks
      // typing past this, but a pasted block of text can still land over
      // the limit in one keystroke, and an example-prompt chip is trusted
      // code so it will never hit this, but user-typed text might.
      setSendError(`Message is too long (max ${CHAT_MESSAGE_MAX_LEN} characters).`);
      return;
    }
    setSending(true);
    setSendError(null);
    setInput("");
    const localId = `local-${Date.now()}`;
    setMessages((prev) => [
      ...prev,
      {
        id: localId,
        role: "user",
        text: trimmed,
        created_at: new Date().toISOString(),
      },
    ]);
    try {
      const res = await withTimeout(
        api.postChat({ persona, message: trimmed, thread_id: threadId ?? undefined }),
        CHAT_SEND_TIMEOUT_MS,
      );
      setMessages((prev) => [...prev, res.message]);
      if (!threadId && res.message.thread_id) {
        onThreadCreated(res.message.thread_id);
      }
      onThreadActivity();
    } catch (err) {
      setSendError(
        err instanceof Error && err.message
          ? err.message
          : "Couldn't get a response. Your message wasn't lost -- try sending again.",
      );
      setInput(trimmed);
      setMessages((prev) => prev.filter((m) => m.id !== localId));
    } finally {
      setSending(false);
    }
  }

  function handleSend() {
    sendMessage(input);
  }

  const inputDisabled = sending || historyStatus === "loading" || historyStatus === "error";
  const showExamples = historyStatus === "ready" && messages.length === 0 && !sending;

  return (
    <div className="chat-panel">
      <div className="chat-panel-messages" ref={scrollRef}>
        {historyStatus === "loading" && <LoadingState label="Loading conversation…" />}
        {historyStatus === "error" && (
          <ErrorState label="Couldn't load your conversation history." onRetry={loadHistory} />
        )}
        {historyStatus === "empty" && (
          <EmptyState label="Start a new conversation from the left to ask about live data." />
        )}
        {showExamples && (
          <div className="chat-examples">
            <p className="chat-examples-hint">Try asking:</p>
            <div className="chat-examples-chips">
              {EXAMPLE_PROMPTS[persona].map((prompt) => (
                <button
                  key={prompt}
                  type="button"
                  className="chat-example-chip"
                  onClick={() => sendMessage(prompt)}
                >
                  {prompt}
                </button>
              ))}
            </div>
          </div>
        )}
        {historyStatus === "ready" &&
          messages.map((msg) => (
            <div key={msg.id} className={`chat-message chat-message-${msg.role}`}>
              <div className="chat-bubble">
                <p>{msg.text}</p>
                <div className="chat-bubble-footer">
                  <span className="chat-bubble-time">{formatTime(msg.created_at)}</span>
                  {msg.thread_id && (
                    <button
                      className="link-btn"
                      onClick={() =>
                        openTrace({
                          threadId: msg.thread_id!,
                          title: "Chat Answer Trace",
                          actions: "none",
                        })
                      }
                    >
                      <IconSearch width={12} height={12} /> trace
                    </button>
                  )}
                </div>
              </div>
            </div>
          ))}
        {sending && (
          <div className="chat-message chat-message-agent">
            <div className="chat-bubble chat-bubble-typing">Reasoning over live data…</div>
          </div>
        )}
      </div>
      {sendError && <p className="chat-panel-error">{sendError}</p>}
      <form
        className="chat-panel-input-row"
        onSubmit={(e) => {
          e.preventDefault();
          handleSend();
        }}
      >
        <label htmlFor="chat-panel-input" className="sr-only">
          Message the agent
        </label>
        <input
          id="chat-panel-input"
          className="chat-panel-input"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Ask about routes, cost, safety, vendors…"
          maxLength={CHAT_MESSAGE_MAX_LEN}
          disabled={inputDisabled}
        />
        <button className="btn btn-primary" type="submit" disabled={inputDisabled || !input.trim()}>
          Send
        </button>
      </form>
    </div>
  );
}
