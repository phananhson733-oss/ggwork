import { describe, expect, it } from "@rstest/core";

import { notesFor, pickAnswerCheckSchema } from "@/core/pick/answer-checks";

const check = (message_id: string | null, run_id: string, notes: string[]) =>
  pickAnswerCheckSchema.parse({
    message_id,
    run_id,
    notes,
    created_at: "2026-09-23T04:00:00+00:00",
    later_field: "ignored",
  });

describe("answer-check notes", () => {
  it("attaches notes to the answer with the same message id only", () => {
    const checks = [check("m1", "r1", ["a"]), check("m2", "r1", ["b"])];
    expect(notesFor(checks, "m1", "r1")).toEqual(["a"]);
    expect(notesFor(checks, "m3", "r1")).toEqual([]);
  });
  it("falls back to the run when the answer had no message id", () => {
    const checks = [check(null, "r1", ["c"])];
    expect(notesFor(checks, "m9", "r1")).toEqual(["c"]);
    expect(notesFor(checks, "m9", undefined)).toEqual([]);
  });
  it("accepts the longest note the backend writes", () => {
    // Five unknown titles of up to 500 characters each, plus the sentence around them.
    const titles = Array.from({ length: 5 }, () => `《${"长".repeat(500)}》`);
    const note = `正文提到的${titles.join("、")}不在本轮查询结果中，请以候选卡为准。`;
    expect(check("m1", "r1", [note]).notes[0]).toBe(note);
  });
});
