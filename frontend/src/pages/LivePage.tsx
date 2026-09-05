import { LiveEventFeed } from "../components/LiveEventFeed";

export function LivePage() {
  return (
    <div>
      <div className="dashboard-heading">
        <h2>Live</h2>
        <p>
          The agent, watched in real time. Events stream in as it senses new data, reasons about
          it, and either acts autonomously or asks for your sign-off. Press “Simulate live day” to
          replay real historical trips at demo pace and watch the pipeline react.
        </p>
      </div>
      <LiveEventFeed />
    </div>
  );
}
