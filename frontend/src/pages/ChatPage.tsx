import { ChatPanel } from "../components/ChatPanel";

export function ChatPage() {
  return (
    <div>
      <div className="dashboard-heading">
        <h2>Ask the Agent</h2>
        <p>Natural-language questions, answered from live data with a visible reasoning trace.</p>
      </div>
      <ChatPanel />
    </div>
  );
}
