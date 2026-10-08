"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import { useAuth } from "@/core/auth/AuthProvider";
import { getPickResult, listPickResults } from "@/core/pick/api";
import {
  bindPickReference,
  bindPickReferences,
  chooseReference,
  freezePickReferences,
  type PickReferences,
  type PickTurnContext,
} from "@/core/pick/references";
import type { PickResult } from "@/core/pick/types";

type PickContextValue = {
  ownerId: string;
  plural: {
    threadId: string;
    value: PickReferences;
    results: PickResult[];
  } | null;
  bindReferences: (
    threadId: string,
    results: PickResult[],
    refs: PickReferences,
  ) => void;
  clearReferences: () => void;
  referenceErrorThread: string | null;
  referenceLoadingThread: string | null;
  startReferenceRestore: (
    threadId: string,
    controller: AbortController,
  ) => void;
  failReferences: (threadId: string) => void;
  contextFor: (threadId: string) => PickTurnContext | undefined;
  result: PickResult | null;
  selected: string[];
  open: boolean;
  show: (result: PickResult, selected?: string[]) => void;
  toggle: (id: string) => void;
  close: () => void;
  current: () => PickResult | null;
  /** A finished candidate card rendered in the chat; the newest per thread is the default follow-up target. */
  observe: (result: PickResult) => void;
  referenceFor: (
    threadId: string,
  ) => ReturnType<typeof bindPickReference> | undefined;
};
const PickContext = createContext<PickContextValue | null>(null);

function storageKey(ownerId: string, threadId: string) {
  return `ggwork-pick:${JSON.stringify([ownerId, threadId])}`;
}

function persist(
  ownerId: string,
  result: PickResult,
  itemIds: string[],
  open: boolean,
) {
  try {
    sessionStorage.setItem(
      storageKey(ownerId, result.thread_id),
      JSON.stringify({ result_id: result.id, item_ids: itemIds, open }),
    );
  } catch {
    /* Selection data remains on the server when browser storage is unavailable. */
  }
}

function OwnedPickProvider({
  children,
  ownerId,
}: {
  children: React.ReactNode;
  ownerId: string;
}) {
  const [referenceErrorThread, setReferenceErrorThread] = useState<
    string | null
  >(null);
  const [referenceLoadingThread, setReferenceLoadingThread] = useState<
    string | null
  >(null);
  const restoreRequest = useRef<AbortController | null>(null);
  const startReferenceRestore = useCallback(
    (threadId: string, controller: AbortController) => {
      restoreRequest.current?.abort();
      restoreRequest.current = controller;
      setReferenceErrorThread(null);
      setReferenceLoadingThread(threadId);
    },
    [],
  );
  const failReferences = useCallback((threadId: string) => {
    setReferenceLoadingThread(null);
    setReferenceErrorThread(threadId);
  }, []);
  const [plural, setPlural] = useState<PickContextValue["plural"]>(null);
  const [result, setResult] = useState<PickResult | null>(null);
  const [selected, setSelected] = useState<string[]>([]);
  const [open, setOpen] = useState(false);
  const currentRef = useRef<PickResult | null>(null);
  const latestRef = useRef(new Map<string, PickResult>());
  const current = useCallback(() => currentRef.current, []);
  const show = useCallback(
    (next: PickResult, ids: string[] = []) => {
      const selectedIds = bindPickReference(next.thread_id, next, ids).item_ids;
      setPlural(null);
      setReferenceErrorThread(null);
      setReferenceLoadingThread(null);
      restoreRequest.current?.abort();
      currentRef.current = next;
      setResult(next);
      setSelected(selectedIds);
      setOpen(true);
      persist(ownerId, next, selectedIds, true);
    },
    [ownerId],
  );
  const toggle = useCallback(
    (id: string) =>
      setSelected((old) =>
        old.includes(id) ? old.filter((key) => key !== id) : [...old, id],
      ),
    [],
  );
  const close = useCallback(() => {
    setOpen(false);
    if (currentRef.current && !plural)
      persist(ownerId, currentRef.current, selected, false);
  }, [ownerId, selected, plural]);
  useEffect(() => {
    if (result && !plural) persist(ownerId, result, selected, open);
  }, [ownerId, result, selected, open, plural]);
  const observe = useCallback((next: PickResult) => {
    if (next.run_status !== "success") return;
    const known = latestRef.current.get(next.thread_id);
    if (!known || known.created_at < next.created_at)
      latestRef.current.set(next.thread_id, next);
  }, []);
  const referenceFor = useCallback(
    (threadId: string) =>
      chooseReference(
        threadId,
        { result, open, selected },
        latestRef.current.get(threadId) ?? null,
      ),
    [result, open, selected],
  );
  const bindReferences = useCallback(
    (threadId: string, results: PickResult[], refs: PickReferences) => {
      const parsed = freezePickReferences(refs);
      const groups = parsed.references.map((ref) => {
        const result = results.find((row) => row.id === ref.result_id);
        if (
          result?.thread_id !== threadId ||
          result.run_status !== "success"
        )
          throw new Error("候选引用不可用，请重新选择");
        return { result, item_ids: ref.item_ids };
      });
      const frozen = bindPickReferences(threadId, groups);
      restoreRequest.current?.abort();
      setReferenceLoadingThread(null);
      setReferenceErrorThread(null);
      setPlural({
        threadId,
        value: frozen,
        results: groups.map((group) => group.result),
      });
      currentRef.current = groups[0]!.result;
      setResult(groups[0]!.result);
      setSelected([...groups[0]!.item_ids]);
      setOpen(false);
      try {
        sessionStorage.setItem(
          storageKey(ownerId, threadId),
          JSON.stringify({ pick_references: frozen }),
        );
      } catch {
        /* Keep the explicit choice in memory. */
      }
    },
    [ownerId],
  );
  const clearReferences = useCallback(() => {
    const storedThread =
      referenceErrorThread ?? referenceLoadingThread ?? plural?.threadId;
    if (storedThread) {
      try {
        sessionStorage.removeItem(storageKey(ownerId, storedThread));
      } catch {
        /* Clearing in-memory references still works. */
      }
    }
    setPlural(null);
    setReferenceErrorThread(null);
    setReferenceLoadingThread(null);
    restoreRequest.current?.abort();
    currentRef.current = null;
    setResult(null);
    setSelected([]);
    setOpen(false);
  }, [ownerId, plural, referenceErrorThread, referenceLoadingThread]);
  const contextFor = useCallback(
    (threadId: string): PickTurnContext | undefined => {
      if (referenceLoadingThread === threadId)
        throw new Error("正在恢复并核对候选引用，请稍候");
      if (referenceErrorThread === threadId)
        throw new Error("候选引用无法恢复，请重新选择或取消引用");
      if (plural?.threadId === threadId)
        return { pick_references: freezePickReferences(plural.value) };
      const single = referenceFor(threadId);
      return single ? { pick_reference: single } : undefined;
    },
    [plural, referenceFor, referenceErrorThread, referenceLoadingThread],
  );
  const value = useMemo(
    () => ({
      ownerId,
      plural,
      bindReferences,
      clearReferences,
      referenceErrorThread,
      referenceLoadingThread,
      startReferenceRestore,
      failReferences,
      contextFor,
      result,
      selected,
      open,
      show,
      toggle,
      close,
      current,
      observe,
      referenceFor,
    }),
    [
      ownerId,
      plural,
      bindReferences,
      clearReferences,
      referenceErrorThread,
      referenceLoadingThread,
      startReferenceRestore,
      failReferences,
      contextFor,
      result,
      selected,
      open,
      show,
      toggle,
      close,
      current,
      observe,
      referenceFor,
    ],
  );
  return <PickContext.Provider value={value}>{children}</PickContext.Provider>;
}

export function PickProvider({ children }: { children: React.ReactNode }) {
  const { user } = useAuth();
  return (
    <OwnedPickProvider
      key={user?.id ?? "anonymous"}
      ownerId={user?.id ?? "anonymous"}
    >
      {children}
    </OwnedPickProvider>
  );
}

export function usePickContext() {
  return useContext(PickContext);
}

export function useRestorePick(threadId: string) {
  const pick = usePickContext();
  const ownerId = pick?.ownerId;
  const show = pick?.show;
  const current = pick?.current;
  const bindReferences = pick?.bindReferences;
  const failReferences = pick?.failReferences;
  const startReferenceRestore = pick?.startReferenceRestore;
  // close changes with checkbox state; restore reads the desired open state once.
  const closeRef = useRef(pick?.close);
  closeRef.current = pick?.close;
  useEffect(() => {
    if (
      !ownerId ||
      ownerId === "anonymous" ||
      !show ||
      !current ||
      current()?.thread_id === threadId
    )
      return;
    const initial = current();
    const abort = new AbortController();
    let restoringPlural = false;
    try {
      const raw = sessionStorage.getItem(storageKey(ownerId, threadId));
      if (!raw) return;
      const saved = JSON.parse(raw) as {
        result_id?: unknown;
        item_ids?: unknown;
        open?: unknown;
        pick_references?: unknown;
      };
      if (saved.pick_references !== undefined && bindReferences) {
        restoringPlural = true;
        const refs = freezePickReferences(saved.pick_references);
        startReferenceRestore?.(threadId, abort);
        void Promise.all(
          refs.references.map((ref) =>
            getPickResult(ref.result_id, abort.signal),
          ),
        )
          .then((results) => {
            if (abort.signal.aborted || current() !== initial) return;
            bindReferences(threadId, results, refs);
          })
          .catch(() => {
            if (!abort.signal.aborted && current() === initial) {
              abort.abort();
              failReferences?.(threadId);
            }
          });
        return () => abort.abort();
      }
      if (
        typeof saved.result_id !== "string" ||
        !Array.isArray(saved.item_ids) ||
        !saved.item_ids.every((id) => typeof id === "string")
      )
        return;
      const ids = saved.item_ids;
      void getPickResult(saved.result_id, abort.signal)
        .then((result) => {
          if (
            abort.signal.aborted ||
            current() !== initial ||
            result.thread_id !== threadId
          )
            return;
          show(result, ids);
          if (saved.open === false) closeRef.current?.();
        })
        .catch(() => {
          /* Do not guess another result after a failed restore. */
        });
    } catch {
      if (restoringPlural) failReferences?.(threadId);
      /* Corrupt local reference does not affect server data. */
    }
    return () => abort.abort();
  }, [
    threadId,
    ownerId,
    show,
    current,
    bindReferences,
    failReferences,
    startReferenceRestore,
  ]);
}

/**
 * Register the thread's finished candidates as they exist on the server, so the newest one is the follow-up
 * target even when its card never mounted (collapsed above a later tool call, or scrolled out of the list).
 * Reads once the answer stops streaming, when the result the turn produced has been stored.
 */
export function useObservePickThread(threadId: string, isLoading: boolean) {
  const pick = usePickContext();
  const client = useQueryClient();
  const ownerId = pick?.ownerId;
  const observe = pick?.observe;
  const enabled =
    Boolean(ownerId && ownerId !== "anonymous" && threadId) && !isLoading;
  const query = useQuery({
    queryKey: ["pick-results", ownerId, threadId],
    queryFn: ({ signal }) => listPickResults(threadId, signal),
    enabled,
    staleTime: 30_000,
    retry: false,
  });
  // Re-enabling may already have started that fetch, so join it rather than cancel it.
  const wasLoading = useRef(isLoading);
  useEffect(() => {
    if (wasLoading.current && !isLoading)
      void client.invalidateQueries(
        { queryKey: ["pick-results", ownerId, threadId] },
        { cancelRefetch: false },
      );
    wasLoading.current = isLoading;
  }, [client, isLoading, ownerId, threadId]);
  const results = query.data;
  useEffect(() => {
    if (!results || !observe) return;
    for (const result of results)
      if (result.thread_id === threadId) observe(result);
  }, [observe, results, threadId]);
}
