"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef } from "react";

import { useAuth } from "@/core/auth/AuthProvider";
import { checkedNotes } from "@/core/pick/answer-checks";
import { listPickAnswerChecks } from "@/core/pick/api";

import { usePickContext } from "./pick-context";

/**
 * Verification notes the backend stored for this answer (titles no tool returned, save or
 * "not posted" claims). They sit beside the answer; the model's text is never rewritten.
 * A clean answer has an empty check and says so; a check that could not be read says that.
 * Rendered only inside the pick workspace, whose provider already requires a signed-in user.
 */
export function PickAnswerCheckNote(props: {
  threadId: string;
  messageId?: string;
  runId?: string;
  isLoading?: boolean;
}) {
  return usePickContext() ? <AnswerCheckNote {...props} /> : null;
}

function AnswerCheckNote({
  threadId,
  messageId,
  runId,
  isLoading = false,
}: {
  threadId: string;
  messageId?: string;
  runId?: string;
  isLoading?: boolean;
}) {
  const { user } = useAuth();
  const client = useQueryClient();
  const userId = user?.id;
  const query = useQuery({
    queryKey: ["pick-answer-checks", userId, threadId],
    queryFn: ({ signal }) => listPickAnswerChecks(threadId, signal),
    enabled: Boolean(userId && threadId) && !isLoading,
    staleTime: 30_000,
    retry: false,
  });
  // The check is stored as the answer finishes; refresh once when this answer stops streaming.
  // Re-enabling may already have started that fetch, so join it rather than cancel it.
  const wasLoading = useRef(isLoading);
  useEffect(() => {
    if (wasLoading.current && !isLoading)
      void client.invalidateQueries(
        { queryKey: ["pick-answer-checks", userId, threadId] },
        { cancelRefetch: false },
      );
    wasLoading.current = isLoading;
  }, [client, isLoading, threadId, userId]);
  if (isLoading) return null;
  if (query.isError)
    // Not "clean": the check may have flagged this answer.
    return (
      <p
        data-testid="pick-answer-check-unavailable"
        className="text-muted-foreground mt-2 text-xs"
      >
        回答核对暂不可用，正文里的剧名和说法请以候选卡为准。
      </p>
    );
  const notes = checkedNotes(query.data ?? [], messageId, runId);
  if (notes === null) return null;
  if (notes.length === 0)
    return (
      <p
        data-testid="pick-answer-check-clean"
        className="text-muted-foreground mt-2 text-xs"
      >
        已核对剧名、保存与发布说法，未发现与工具结果不符；其他内容以候选卡为准。
      </p>
    );
  return (
    <aside
      role="note"
      data-testid="pick-answer-check"
      className="border-warning-line bg-warning-surface text-warning-ink mt-2 rounded-lg border p-3 text-xs leading-5"
    >
      <p className="font-medium">核对提示</p>
      <ul className="mt-1 list-disc space-y-1 pl-4">
        {notes.map((note) => (
          <li key={note}>{note}</li>
        ))}
      </ul>
    </aside>
  );
}
