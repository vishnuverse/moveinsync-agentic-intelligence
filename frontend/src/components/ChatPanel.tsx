import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import type { ChatMessage } from "../api";
import { useAppState } from "../state/AppStateContext";
import "./ChatPanel.css";

function formatTime(ts: string): string {
  return new Date(ts).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

export function ChatPanel() {
  const { persona, openTrace } = useAppState();
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [loadingHistory, setLoadingHistory] = useState(true);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    setLoadingHistory(true);
    api.getChatHistory(persona).then((res) => {
      setMessages(res);
      setLoadingHistory(false);
    });
  }, [persona]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages]);

  async function handleSend() {
    const trimmed = input.trim();
    if (!trimmed || sending) return;
    setSending(true);
    setInput("");
    setMessages((prev) => [
      ...prev,
      {
        id: `local-${Date.now()}`,
        role: "user",
        text: trimmed,
        created_at: new Date().toISOString(),
      },
    ]);
    try {
      const res = await api.postChat({ persona, message: trimmed });
      setMessages((prev) => [...prev, res.message]);
    } finally {
      setSending(false);
    }
  }

  return (
    <div className="chat-panel">
      <div className="chat-panel-messages" ref={scrollRef}>
        {loadingHistory && <p className="notification-empty">Loading conversation…</p>}
        {!loadingHistory &&
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
                      🔍 trace
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
      <form
        className="chat-panel-input-row"
        onSubmit={(e) => {
          e.preventDefault();
          handleSend();
        }}
      >
        <input
          className="chat-panel-input"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Ask about routes, cost, safety, vendors…"
          disabled={sending}
        />
        <button className="btn btn-primary" type="submit" disabled={sending || !input.trim()}>
          Send
        </button>
      </form>
    </div>
  );
}
