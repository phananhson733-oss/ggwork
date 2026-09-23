"use client";

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
import { getPickResult } from "@/core/pick/api";
import { bindPickReference, chooseReference } from "@/core/pick/references";
import type { PickResult } from "@/core/pick/types";

type PickContextValue = {
  ownerId: string;
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
  const [result, setResult] = useState<PickResult | null>(null);
  const [selected, setSelected] = useState<string[]>([]);
  const [open, setOpen] = useState(false);
  const currentRef = useRef<PickResult | null>(null);
  const latestRef = useRef(new Map<string, PickResult>());
  const current = useCallback(() => currentRef.current, []);
  const show = useCallback(
    (next: PickResult, ids: string[] = []) => {
      const selectedIds = bindPickReference(next.thread_id, next, ids).item_ids;
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
    if (currentRef.current)
      persist(ownerId, currentRef.current, selected, false);
  }, [ownerId, selected]);
  useEffect(() => {
    if (result) persist(ownerId, result, selected, open);
  }, [ownerId, result, selected, open]);
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
  const value = useMemo(
    () => ({
      ownerId,
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
    try {
      const raw = sessionStorage.getItem(storageKey(ownerId, threadId));
      if (!raw) return;
      const saved = JSON.parse(raw) as {
        result_id?: unknown;
        item_ids?: unknown;
        open?: unknown;
      };
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
      /* Corrupt local reference does not affect server data. */
    }
    return () => abort.abort();
  }, [threadId, ownerId, show, current]);
}
