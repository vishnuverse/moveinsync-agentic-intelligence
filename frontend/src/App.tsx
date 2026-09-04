import { useEffect, useState } from "react";
import "./App.css";
import logoMark from "./assets/moveinsync-mark.svg";
import { RoleSwitcher } from "./components/RoleSwitcher";
import { TraceDrawer } from "./components/TraceDrawer";
import { AppStateProvider, useAppState } from "./state/AppStateContext";
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
  const { uiState, closeTrace } = useAppState();

  // Escape closes the trace drawer from anywhere -- the only keyboard
  // shortcut this operate-mode surface has today, but a cheap and
  // high-value one for a panel with no other dismiss affordance besides a
  // 44x44 button or clicking the scrim.
  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape" && uiState.trace.open) closeTrace();
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [uiState.trace.open, closeTrace]);

  return (
    <div className="app-shell">
      <header className="app-header">
        <div className="app-header-brand">
          <img src={logoMark} alt="MoveInSync" className="app-header-logo" width={30} height={28} />
          <div className="app-header-brand-text">
            <span className="app-header-wordmark">MoveInSync</span>
            <span className="app-header-subtitle">Agentic Intelligence</span>
          </div>
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
