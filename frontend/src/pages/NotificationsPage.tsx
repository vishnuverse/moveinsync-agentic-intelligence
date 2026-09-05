import { NotificationInbox } from "../components/NotificationInbox";

export function NotificationsPage() {
  return (
    <div>
      <div className="dashboard-heading">
        <h2>Notifications</h2>
        <p>
          Your persona's action inbox — alerts the agent decided are relevant to you specifically.
          Items needing your sign-off are highlighted; everything else is informational. For the
          system-wide log of every autonomous run (not just yours), see Activity Log.
        </p>
      </div>
      <NotificationInbox />
    </div>
  );
}
