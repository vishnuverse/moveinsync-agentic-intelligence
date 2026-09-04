import { AgentActivity } from "../components/AgentActivity";

export function ActivityPage() {
  return (
    <div>
      <div className="dashboard-heading">
        <h2>Agent Activity</h2>
        <p>
          System-wide autonomous run log, across all personas — proof the agent acts without a
          person prompting it. Each row fired on a schedule tick or a live data event, never a
          click.
        </p>
      </div>
      <AgentActivity />
    </div>
  );
}
