import { useAppState } from "../state/AppStateContext";
import { LineManagerDashboard } from "../dashboards/LineManagerDashboard";
import { TransportHeadDashboard } from "../dashboards/TransportHeadDashboard";
import { TransportManagerDashboard } from "../dashboards/TransportManagerDashboard";

export function DashboardPage() {
  const { persona } = useAppState();
  if (persona === "line_manager") return <LineManagerDashboard />;
  if (persona === "transport_head") return <TransportHeadDashboard />;
  return <TransportManagerDashboard />;
}
