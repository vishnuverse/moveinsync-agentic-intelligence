import { AgentActivity } from "../components/AgentActivity";

export function ActivityPage() {
  return (
    <div>
      <div className="dashboard-heading">
        <h2>Activity Log</h2>
        <p>
          A read-only audit trail of every autonomous pipeline run, across ALL personas — not a
          to-do list (that's Notifications). Each row fired on its own, on a schedule tick or a
          live data event, never a click, and shows what the agent found and decided.
        </p>
      </div>
      <AgentActivity />
    </div>
  );
}
