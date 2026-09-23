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

/** Notes for one rendered answer: matched by message id; a check stored without one falls back to its run. */
export function notesFor(
  checks: readonly PickAnswerCheck[],
  messageId: string | undefined,
  runId: string | undefined,
): string[] {
  return checks
    .filter((check) =>
      check.message_id
        ? check.message_id === messageId
        : Boolean(runId) && check.run_id === runId,
    )
    .flatMap((check) => check.notes);
}
