import { createContext, useCallback, useContext, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import type { ActivityEntry, NotificationStatus, PersonaId } from "../api";

// A resolved approve/reject decision, kept client-side so it shows up
// durably in Agent Activity instead of only as one ephemeral sentence in
// the trace drawer that then closes. The backend's own pipeline_runs log
// (app/services/activity_log.py) never writes a row for a resume decision
// -- only autonomous scheduler/event runs -- so without this, a resolved
// decision leaves no trace anywhere once the drawer shuts.
const LOCAL_ACTIVITY_KEY = "moveinsync.localActivity.v1";

function loadLocalActivity(): ActivityEntry[] {
  try {
    const raw = sessionStorage.getItem(LOCAL_ACTIVITY_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

export interface TraceDrawerState {
  open: boolean;
  threadId: string | null;
  title: string | null;
  actions: "none" | "approve-reject";
  actionTargetId: string | null;
}

export interface PersonaUiState {
  selectedNotificationId: string | null;
  trace: TraceDrawerState;
}

const emptyTrace: TraceDrawerState = {
  open: false,
  threadId: null,
  title: null,
  actions: "none",
  actionTargetId: null,
};

function emptyPersonaState(): PersonaUiState {
  return {
    selectedNotificationId: null,
    trace: { ...emptyTrace },
  };
}

interface AppStateValue {
  persona: PersonaId;
  setPersona: (p: PersonaId) => void;
  uiState: PersonaUiState;
  setSelectedNotification: (id: string | null) => void;
  openTrace: (opts: {
    threadId: string;
    title?: string;
    actions?: "none" | "approve-reject";
    actionTargetId?: string | null;
  }) => void;
  closeTrace: () => void;
  notifyResolved: (id: string, status: NotificationStatus) => void;
  onResolved: (fn: (id: string, status: NotificationStatus) => void) => () => void;
  localActivity: ActivityEntry[];
  recordActivity: (entry: ActivityEntry) => void;
}

const AppStateContext = createContext<AppStateValue | null>(null);

export function AppStateProvider({ children }: { children: ReactNode }) {
  const [persona, setPersonaState] = useState<PersonaId>("transport_manager");
  const [statesByPersona, setStatesByPersona] = useState<Record<PersonaId, PersonaUiState>>({
    transport_manager: emptyPersonaState(),
    line_manager: emptyPersonaState(),
    transport_head: emptyPersonaState(),
  });

  const setPersona = useCallback((p: PersonaId) => setPersonaState(p), []);

  const updateCurrent = useCallback(
    (updater: (prev: PersonaUiState) => PersonaUiState) => {
      setStatesByPersona((prev) => ({
        ...prev,
        [persona]: updater(prev[persona]),
      }));
    },
    [persona],
  );

  const setSelectedNotification = useCallback(
    (id: string | null) => {
      updateCurrent((prev) => ({ ...prev, selectedNotificationId: id }));
    },
    [updateCurrent],
  );

  const openTrace = useCallback<AppStateValue["openTrace"]>(
    (opts) => {
      updateCurrent((prev) => ({
        ...prev,
        trace: {
          open: true,
          threadId: opts.threadId,
          title: opts.title ?? null,
          actions: opts.actions ?? "none",
          actionTargetId: opts.actionTargetId ?? null,
        },
      }));
    },
    [updateCurrent],
  );

  const closeTrace = useCallback(() => {
    updateCurrent((prev) => ({ ...prev, trace: { ...prev.trace, open: false } }));
  }, [updateCurrent]);

  const resolvedListeners = useRef(new Set<(id: string, status: NotificationStatus) => void>());

  const notifyResolved = useCallback((id: string, status: NotificationStatus) => {
    resolvedListeners.current.forEach((fn) => fn(id, status));
  }, []);

  const onResolved = useCallback(
    (fn: (id: string, status: NotificationStatus) => void) => {
      resolvedListeners.current.add(fn);
      return () => resolvedListeners.current.delete(fn);
    },
    [],
  );

  const [localActivity, setLocalActivity] = useState<ActivityEntry[]>(loadLocalActivity);

  const recordActivity = useCallback((entry: ActivityEntry) => {
    setLocalActivity((prev) => {
      const next = [entry, ...prev].slice(0, 100);
      try {
        sessionStorage.setItem(LOCAL_ACTIVITY_KEY, JSON.stringify(next));
      } catch {
        // best-effort persistence -- private browsing / storage quota, fine to drop
      }
      return next;
    });
  }, []);

  const value = useMemo<AppStateValue>(
    () => ({
      persona,
      setPersona,
      uiState: statesByPersona[persona],
      setSelectedNotification,
      openTrace,
      closeTrace,
      notifyResolved,
      onResolved,
      localActivity,
      recordActivity,
    }),
    [
      persona,
      setPersona,
      statesByPersona,
      setSelectedNotification,
      openTrace,
      closeTrace,
      notifyResolved,
      onResolved,
      localActivity,
      recordActivity,
    ],
  );

  return <AppStateContext.Provider value={value}>{children}</AppStateContext.Provider>;
}

export function useAppState(): AppStateValue {
  const ctx = useContext(AppStateContext);
  if (!ctx) throw new Error("useAppState must be used within AppStateProvider");
  return ctx;
}
