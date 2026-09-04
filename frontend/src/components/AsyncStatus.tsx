// Shared loading / error / empty presentation for every data-fetching view.
// Before this, a failed request just left `loading` stuck true forever with
// no error branch anywhere in the app -- this gives every fetch a distinct,
// recoverable end state instead.
import { IconAlert } from "./icons";
import "./AsyncStatus.css";

export function LoadingState({ label }: { label: string }) {
  return (
    <div className="async-state async-state-loading" role="status" aria-live="polite">
      <span className="async-spinner" aria-hidden="true" />
      <span>{label}</span>
    </div>
  );
}

export function ErrorState({ label, onRetry }: { label: string; onRetry: () => void }) {
  return (
    <div className="async-state async-state-error" role="alert">
      <IconAlert className="async-state-icon" />
      <span>{label}</span>
      <button type="button" className="btn btn-secondary async-state-retry" onClick={onRetry}>
        Retry
      </button>
    </div>
  );
}

export function EmptyState({ label }: { label: string }) {
  return <p className="async-state async-state-empty">{label}</p>;
}
