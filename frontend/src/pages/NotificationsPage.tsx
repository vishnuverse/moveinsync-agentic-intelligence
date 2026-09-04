import { NotificationInbox } from "../components/NotificationInbox";

export function NotificationsPage() {
  return (
    <div>
      <div className="dashboard-heading">
        <h2>Notifications</h2>
        <p>Alerts and actions from your agent, scoped to your persona. Items needing your sign-off are highlighted.</p>
      </div>
      <NotificationInbox />
    </div>
  );
}
