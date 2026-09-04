import { DashboardShell } from "./DashboardShell";

export function TransportManagerDashboard() {
  return (
    <DashboardShell
      persona="transport_manager"
      heading="Fleet Operations"
      description="Live SLA, cost, and safety signals for the routes and vendors you manage day to day."
    />
  );
}
