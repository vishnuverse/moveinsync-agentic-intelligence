import { useState } from "react";
import { ChatPanel } from "../components/ChatPanel";
import { ChatThreadList } from "../components/ChatThreadList";
import { useAppState } from "../state/AppStateContext";
import "./ChatPage.css";

export function ChatPage() {
  const { persona } = useAppState();
  const [activeThreadId, setActiveThreadId] = useState<string | null>(null);
  // Bumped after every send so ChatThreadList refetches and picks up the
  // server-side updated_at/title bump for the thread that was just posted
  // into -- see ChatPanel's onThreadActivity prop.
  const [refreshSignal, setRefreshSignal] = useState(0);

  return (
    <div>
      <div className="dashboard-heading">
        <h2>Ask the Agent</h2>
        <p>Natural-language questions, answered from live data with a visible reasoning trace.</p>
      </div>
      <div className="chat-page-layout">
        <ChatThreadList
          persona={persona}
          activeThreadId={activeThreadId}
          onSelect={setActiveThreadId}
          refreshSignal={refreshSignal}
        />
        <ChatPanel
          key={persona}
          threadId={activeThreadId}
          onThreadCreated={setActiveThreadId}
          onThreadActivity={() => setRefreshSignal((n) => n + 1)}
        />
      </div>
    </div>
  );
}
