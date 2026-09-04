import { createContext, useCallback, useContext, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import type { NotificationStatus, PersonaId } from "../api";

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
    ],
  );

  return <AppStateContext.Provider value={value}>{children}</AppStateContext.Provider>;
}

export function useAppState(): AppStateValue {
  const ctx = useContext(AppStateContext);
  if (!ctx) throw new Error("useAppState must be used within AppStateProvider");
  return ctx;
}
