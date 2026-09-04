import { useState } from "react";
import "./App.css";
import { RoleSwitcher } from "./components/RoleSwitcher";
import { TraceDrawer } from "./components/TraceDrawer";
import { AppStateProvider } from "./state/AppStateContext";
import { ActivityPage } from "./pages/ActivityPage";
import { ChatPage } from "./pages/ChatPage";
import { DashboardPage } from "./pages/DashboardPage";
import { NotificationsPage } from "./pages/NotificationsPage";

type NavView = "dashboard" | "notifications" | "activity" | "chat";

const NAV_ITEMS: Array<{ id: NavView; label: string }> = [
  { id: "dashboard", label: "Dashboard" },
  { id: "notifications", label: "Notifications" },
  { id: "activity", label: "Agent Activity" },
  { id: "chat", label: "Chat" },
];

function AppShell() {
  const [view, setView] = useState<NavView>("dashboard");

  return (
    <div className="app-shell">
      <header className="app-header">
        <div className="app-header-brand">
          <span className="app-header-logo">MoveInSync</span>
          <span className="app-header-subtitle">Agentic Intelligence</span>
        </div>
        <nav className="app-nav">
          {NAV_ITEMS.map((item) => (
            <button
              key={item.id}
              className={`app-nav-item ${view === item.id ? "app-nav-item-active" : ""}`}
              onClick={() => setView(item.id)}
            >
              {item.label}
            </button>
          ))}
        </nav>
        <RoleSwitcher />
      </header>

      <main className="app-main">
        {view === "dashboard" && <DashboardPage />}
        {view === "notifications" && <NotificationsPage />}
        {view === "activity" && <ActivityPage />}
        {view === "chat" && <ChatPage />}
      </main>

      <TraceDrawer />
    </div>
  );
}

export default function App() {
  return (
    <AppStateProvider>
      <AppShell />
    </AppStateProvider>
  );
}
