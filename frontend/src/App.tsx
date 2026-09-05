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
import { OutboxPage } from "./pages/OutboxPage";
import { SettingsPage } from "./pages/SettingsPage";

// "live" (LivePage) removed from navigation for now -- kept in the codebase
// (pages/LivePage.tsx untouched) so it can be re-added later without
// rebuilding it; simply not reachable from the nav bar today.
type NavView = "dashboard" | "notifications" | "outbox" | "activity" | "chat" | "settings";

const NAV_ITEMS: Array<{ id: NavView; label: string }> = [
  { id: "dashboard", label: "Dashboard" },
  { id: "notifications", label: "Notifications" },
  { id: "outbox", label: "Outbox" },
  { id: "activity", label: "Activity Log" },
  { id: "chat", label: "Chat" },
  { id: "settings", label: "Settings" },
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
        {view === "outbox" && <OutboxPage />}
        {view === "activity" && <ActivityPage />}
        {view === "chat" && <ChatPage />}
        {view === "settings" && <SettingsPage />}
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
