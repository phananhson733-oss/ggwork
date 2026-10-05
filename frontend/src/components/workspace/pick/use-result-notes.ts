"use client";

import { useQuery } from "@tanstack/react-query";

import { useAuth } from "@/core/auth/AuthProvider";
import { getPickResultNotes } from "@/core/pick/api";
import type { PickNotesState } from "@/core/pick/notes";

/** A result's notes for CandidateView; undefined while they load. A failed read is a state, not a thrown error. */
export function useResultNotes(
  resultId: string | undefined,
): PickNotesState | undefined {
  const { user } = useAuth();
  const query = useQuery({
    queryKey: ["pick-result-notes", user?.id, resultId],
    queryFn: ({ signal }) => getPickResultNotes(resultId!, signal),
    enabled: Boolean(user?.id && resultId),
    staleTime: 60_000,
    retry: false,
  });
  return query.error ? { kind: "error" } : query.data;
}
