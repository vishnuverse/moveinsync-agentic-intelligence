import { useEffect, useState } from "react";
import { api } from "../api";
import type { ChatThread, PersonaId, ScopeOption } from "../api";
import { withTimeout } from "../lib/timeout";
import { IconClose } from "./icons";
import "./NewThreadModal.css";

interface NewThreadModalProps {
  persona: PersonaId;
  onClose: () => void;
  onCreated: (thread: ChatThread) => void;
}

// "Select something to chat with": lets a new conversation optionally lock
// onto one vendor/route/team/region (persona-dependent -- see
// backend/app/services/scope_options.py) so every question in that thread
// gets biased toward it server-side, without the agent needing any new
// reasoning logic of its own.
export function NewThreadModal({ persona, onClose, onCreated }: NewThreadModalProps) {
  const [options, setOptions] = useState<ScopeOption[]>([]);
  const [optionsStatus, setOptionsStatus] = useState<"loading" | "ready" | "error">("loading");
  const [selected, setSelected] = useState<ScopeOption | null>(null);
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setOptionsStatus("loading");
    withTimeout(api.getScopeOptions(persona))
      .then((res) => {
        setOptions(res);
        setOptionsStatus("ready");
      })
      .catch(() => setOptionsStatus("error"));
  }, [persona]);

  async function handleCreate() {
    setCreating(true);
    setError(null);
    try {
      const thread = await withTimeout(
        api.createChatThread({
          persona,
          scope_entity_type: selected?.type,
          scope_entity_id: selected?.id,
        }),
      );
      onCreated(thread);
    } catch {
      setError("Couldn't start a new conversation. Please try again.");
      setCreating(false);
    }
  }

  const grouped = options.reduce<Record<string, ScopeOption[]>>((acc, opt) => {
    (acc[opt.type] ??= []).push(opt);
    return acc;
  }, {});

  return (
    <div className="new-thread-scrim" onClick={onClose}>
      <div
        className="new-thread-modal"
        role="dialog"
        aria-modal="true"
        aria-label="Start a new conversation"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="new-thread-modal-header">
          <h3>New conversation</h3>
          <button type="button" className="chat-thread-action-btn" aria-label="Close" onClick={onClose}>
            <IconClose width={14} height={14} />
          </button>
        </div>

        <p className="new-thread-modal-hint">
          Optionally focus this conversation on something specific — the agent will keep it in mind for every
          question you ask in this thread.
        </p>

        {optionsStatus === "loading" && <p className="new-thread-modal-status">Loading options…</p>}
        {optionsStatus === "error" && (
          <p className="new-thread-modal-status">
            Couldn't load scope options — you can still start a general conversation below.
          </p>
        )}
        {optionsStatus === "ready" && options.length === 0 && (
          <p className="new-thread-modal-status">No vendors/routes/teams to pick from right now.</p>
        )}

        {optionsStatus === "ready" && options.length > 0 && (
          <div className="new-thread-scope-groups">
            <button
              type="button"
              className={`new-thread-scope-chip ${!selected ? "new-thread-scope-chip-active" : ""}`}
              onClick={() => setSelected(null)}
            >
              General question
            </button>
            {Object.entries(grouped).map(([type, opts]) => (
              <div key={type} className="new-thread-scope-group">
                <span className="new-thread-scope-group-label">{type}</span>
                <div className="new-thread-scope-chip-row">
                  {opts.map((opt) => (
                    <button
                      key={`${opt.type}-${opt.id}`}
                      type="button"
                      className={`new-thread-scope-chip ${
                        selected?.type === opt.type && selected?.id === opt.id ? "new-thread-scope-chip-active" : ""
                      }`}
                      onClick={() => setSelected(opt)}
                    >
                      {opt.label}
                    </button>
                  ))}
                </div>
              </div>
            ))}
          </div>
        )}

        {error && <p className="chat-panel-error">{error}</p>}

        <div className="new-thread-modal-actions">
          <button type="button" className="btn btn-secondary" onClick={onClose} disabled={creating}>
            Cancel
          </button>
          <button type="button" className="btn btn-primary" onClick={handleCreate} disabled={creating}>
            {creating ? "Starting…" : "Start conversation"}
          </button>
        </div>
      </div>
    </div>
  );
}
