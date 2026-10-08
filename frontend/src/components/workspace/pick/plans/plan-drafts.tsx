"use client";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";

import { useAuth } from "@/core/auth/AuthProvider";
import { type Plan, type PlanUpdate } from "@/core/pick/completion-types";
import { type editablePlan } from "@/core/pick/plan-draft";

type Draft = {
  base: Plan;
  draft: ReturnType<typeof editablePlan>;
  timezoneChange: PlanUpdate["timezone_change"];
  pending: { key: string; request_id: string } | null;
};
const Context = createContext<{
  drafts: Record<string, Draft>;
  setDraft: (id: string, draft: Draft | null) => void;
} | null>(null);
function OwnedPlanDrafts({ children }: { children: ReactNode }) {
  const [drafts, setDrafts] = useState<Record<string, Draft>>({});
  const setDraft = useCallback(
    (id: string, draft: Draft | null) =>
      setDrafts((current) => {
        const next = { ...current };
        if (draft) next[id] = draft;
        else delete next[id];
        return next;
      }),
    [],
  );
  const dirty = Object.keys(drafts).length > 0;
  useEffect(() => {
    if (!dirty) return;
    const unload = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", unload);
    return () => window.removeEventListener("beforeunload", unload);
  }, [dirty]);
  return (
    <Context.Provider value={{ drafts, setDraft }}>{children}</Context.Provider>
  );
}
export function PlanDraftProvider({ children }: { children: ReactNode }) {
  const { user } = useAuth();
  return (
    <OwnedPlanDrafts key={user?.id ?? "anonymous"}>{children}</OwnedPlanDrafts>
  );
}
export function usePlanDrafts() {
  return useContext(Context);
}
