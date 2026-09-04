import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import type { ChatThread, PersonaId } from "../api";
import { withTimeout } from "../lib/timeout";
import { EmptyState, ErrorState, LoadingState } from "./AsyncStatus";
import { IconPencil, IconPlus, IconTrash } from "./icons";
import { NewThreadModal } from "./NewThreadModal";
import "./ChatThreadList.css";

function formatRelative(ts: string): string {
  const diffMs = Date.now() - new Date(ts).getTime();
  const mins = Math.round(diffMs / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.round(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.round(hours / 24);
  return `${days}d ago`;
}

interface ChatThreadListProps {
  persona: PersonaId;
  activeThreadId: string | null;
  onSelect: (threadId: string | null) => void;
  // Bump this number (from the parent) whenever a message was just sent, so
  // the list re-fetches and picks up the server-side updated_at/title bump
  // -- this component otherwise has no way to know a send happened in the
  // sibling ChatPanel.
  refreshSignal: number;
}

export function ChatThreadList({ persona, activeThreadId, onSelect, refreshSignal }: ChatThreadListProps) {
  const [threads, setThreads] = useState<ChatThread[]>([]);
  const [status, setStatus] = useState<"loading" | "ready" | "error">("loading");
  const [showNewModal, setShowNewModal] = useState(false);
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState("");

  const load = useCallback(() => {
    setStatus("loading");
    withTimeout(api.getChatThreads(persona))
      .then((res) => {
        setThreads(res);
        setStatus("ready");
        // Auto-select the most recent thread the first time a persona's
        // list loads (or after the active one was deleted elsewhere) so the
        // panel isn't stuck on an empty state when conversations already
        // exist -- but never override a deliberate in-list selection.
        if (res.length > 0 && !res.some((t) => t.id === activeThreadId)) {
          onSelect(res[0].id);
        } else if (res.length === 0) {
          onSelect(null);
        }
      })
      .catch(() => setStatus("error"));
    // activeThreadId is read, not reacted to, here -- re-running this whole
    // fetch every time the user merely clicks a different thread would be
    // wasteful; only persona/refreshSignal changing should trigger a reload.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [persona]);

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [persona, refreshSignal]);

  function handleCreated(thread: ChatThread) {
    setThreads((prev) => [thread, ...prev]);
    onSelect(thread.id);
    setShowNewModal(false);
  }

  function startRename(thread: ChatThread) {
    setRenamingId(thread.id);
    setRenameValue(thread.title);
  }

  async function commitRename(id: string) {
    const title = renameValue.trim();
    setRenamingId(null);
    const current = threads.find((t) => t.id === id);
    if (!title || (current && title === current.title)) return;
    try {
      const updated = await api.renameChatThread(id, { title });
      setThreads((prev) => prev.map((t) => (t.id === id ? updated : t)));
    } catch {
      // Best-effort: a failed rename just keeps the old title in the list --
      // low-stakes enough not to need its own dedicated error banner.
    }
  }

  async function handleDelete(thread: ChatThread) {
    if (!window.confirm(`Delete "${thread.title}"? This can't be undone.`)) return;
    try {
      await api.deleteChatThread(thread.id);
      setThreads((prev) => {
        const next = prev.filter((t) => t.id !== thread.id);
        if (activeThreadId === thread.id) onSelect(next[0]?.id ?? null);
        return next;
      });
    } catch {
      window.alert("Couldn't delete that conversation. Please try again.");
    }
  }

  return (
    <div className="chat-thread-list">
      <div className="chat-thread-list-header">
        <h3>Conversations</h3>
        <button type="button" className="btn btn-secondary chat-thread-new-btn" onClick={() => setShowNewModal(true)}>
          <IconPlus width={14} height={14} /> New
        </button>
      </div>

      <div className="chat-thread-list-items">
        {status === "loading" && <LoadingState label="Loading conversations…" />}
        {status === "error" && <ErrorState label="Couldn't load your conversations." onRetry={load} />}
        {status === "ready" && threads.length === 0 && (
          <EmptyState label="No conversations yet -- start one to ask about live data." />
        )}
        {status === "ready" &&
          threads.map((thread) => (
            <div
              key={thread.id}
              className={`chat-thread-item ${thread.id === activeThreadId ? "chat-thread-item-active" : ""}`}
            >
              {renamingId === thread.id ? (
                <input
                  autoFocus
                  className="chat-thread-rename-input"
                  value={renameValue}
                  maxLength={200}
                  onChange={(e) => setRenameValue(e.target.value)}
                  onBlur={() => commitRename(thread.id)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") commitRename(thread.id);
                    if (e.key === "Escape") setRenamingId(null);
                  }}
                />
              ) : (
                <button type="button" className="chat-thread-select-btn" onClick={() => onSelect(thread.id)}>
                  <span className="chat-thread-title">{thread.title}</span>
                  <span className="chat-thread-meta">
                    {thread.scope_entity_id && (
                      <span className="badge badge-info chat-thread-scope-badge">{thread.scope_entity_id}</span>
                    )}
                    <span className="chat-thread-time">{formatRelative(thread.updated_at)}</span>
                  </span>
                </button>
              )}
              <div className="chat-thread-item-actions">
                <button
                  type="button"
                  className="chat-thread-action-btn"
                  aria-label={`Rename "${thread.title}"`}
                  onClick={() => startRename(thread)}
                >
                  <IconPencil width={13} height={13} />
                </button>
                <button
                  type="button"
                  className="chat-thread-action-btn"
                  aria-label={`Delete "${thread.title}"`}
                  onClick={() => handleDelete(thread)}
                >
                  <IconTrash width={13} height={13} />
                </button>
              </div>
            </div>
          ))}
      </div>

      {showNewModal && (
        <NewThreadModal persona={persona} onClose={() => setShowNewModal(false)} onCreated={handleCreated} />
      )}
    </div>
  );
}
