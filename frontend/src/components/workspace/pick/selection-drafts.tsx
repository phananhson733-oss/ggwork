"use client";

import {
  createContext,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";

import { useAuth } from "@/core/auth/AuthProvider";
import type { SavedPick } from "@/core/pick/api";

type Draft = { base: SavedPick; note: string };
type Drafts = Record<string, Draft>;
const SelectionDrafts = createContext<{
  drafts: Drafts;
  setDraft: (id: string, draft: Draft | null) => void;
  discard: () => void;
} | null>(null);

function OwnedDrafts({ children }: { children: ReactNode }) {
  const [drafts, setDrafts] = useState<Drafts>({});
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
    <SelectionDrafts.Provider
      value={{
        drafts,
        setDraft: (id, draft) =>
          setDrafts((current) => {
            const next = { ...current };
            if (draft) next[id] = draft;
            else delete next[id];
            return next;
          }),
        discard: () => setDrafts({}),
      }}
    >
      {children}
    </SelectionDrafts.Provider>
  );
}

/** Workspace lifetime only: Back/Forward preserves edits; changing owner destroys them. */
export function SelectionDraftProvider({ children }: { children: ReactNode }) {
  const { user } = useAuth();
  return <OwnedDrafts key={user?.id ?? "anonymous"}>{children}</OwnedDrafts>;
}

export function useSelectionDrafts() {
  const state = useContext(SelectionDrafts);
  if (!state) throw new Error("SelectionDraftProvider is required");
  return state;
}
