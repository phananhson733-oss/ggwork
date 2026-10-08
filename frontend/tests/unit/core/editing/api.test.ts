import { afterEach, expect, it, rs } from "@rstest/core";

import {
  listEditingTasks,
  uploadSource,
  EditingError,
} from "@/core/editing/api";
afterEach(() => {
  rs.restoreAllMocks();
});
it("preserves paginated history and sends cancellation to the gateway", async () => {
  const signal = new AbortController().signal;
  rs.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    expect(
      typeof input === "string"
        ? input
        : input instanceof URL
          ? input.href
          : input.url,
    ).toContain("/api/editing/tasks?limit=25&offset=25");
    expect(init?.signal).toBe(signal);
    return Response.json({
      items: [],
      limit: 25,
      offset: 25,
      total: 30,
      next_offset: null,
    });
  });
  expect((await listEditingTasks(25, signal)).total).toBe(30);
});
it("reports only native acknowledged upload bytes and never labels receipt verified", async () => {
  const progress: number[] = [];
  rs.spyOn(globalThis, "fetch")
    .mockResolvedValueOnce(
      Response.json({
        transfer_id: "a",
        offset: 0,
        size_bytes: 3,
        chunk_bytes: 2,
        state: "receiving",
      }),
    )
    .mockResolvedValueOnce(
      Response.json({
        transfer_id: "a",
        offset: 2,
        size_bytes: 3,
        state: "receiving",
      }),
    )
    .mockResolvedValueOnce(
      Response.json({
        transfer_id: "a",
        offset: 3,
        size_bytes: 3,
        state: "received",
      }),
    );
  const result = await uploadSource(
    "task",
    "media",
    new Blob(["abc"]),
    new AbortController().signal,
    (value) => progress.push(value),
  );
  expect(progress).toEqual([2, 3]);
  expect(result.state).toBe("received");
});
it("does not expose raw failure response text", async () => {
  rs.spyOn(globalThis, "fetch").mockResolvedValue(
    Response.json({ detail: "secret signed URL" }, { status: 403 }),
  );
  await expect(listEditingTasks(0)).rejects.toBeInstanceOf(EditingError);
  await expect(listEditingTasks(0)).rejects.not.toThrow("secret signed URL");
});
