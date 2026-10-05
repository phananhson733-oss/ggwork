import { z } from "zod";

// Not strict: an additive backend field must not hide the notes.
export const pickAnswerCheckSchema = z.object({
  // Null when the model message had no id; the note is then matched by run.
  message_id: z.string().max(256).nullable(),
  run_id: z.string().max(128),
  // A note can list five titles of up to 500 characters each.
  notes: z.array(z.string().max(4000)).max(10),
  created_at: z.string(),
});

export type PickAnswerCheck = z.infer<typeof pickAnswerCheckSchema>;

/**
 * The notes of one rendered answer's checks, matched by message id; a check stored without one falls back to its
 * run. Null when no check matched (an older answer, a tool-call turn); an empty list when it was checked and clean.
 */
export function checkedNotes(
  checks: readonly PickAnswerCheck[],
  messageId: string | undefined,
  runId: string | undefined,
): string[] | null {
  const matched = checks.filter((check) =>
    check.message_id
      ? check.message_id === messageId
      : Boolean(runId) && check.run_id === runId,
  );
  return matched.length ? matched.flatMap((check) => check.notes) : null;
}

/** Notes for one rendered answer; none when it was clean or never checked. */
export function notesFor(
  checks: readonly PickAnswerCheck[],
  messageId: string | undefined,
  runId: string | undefined,
): string[] {
  return checkedNotes(checks, messageId, runId) ?? [];
}
