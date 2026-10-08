import { expect, it } from "@rstest/core";

import {
  editingCreateHref,
  editingTaskId,
  safeEditingReturn,
} from "@/core/editing/presentation";
it("keeps the selected drama and exact filtered return context", () => {
  const href = editingCreateHref(
    "雨夜",
    "/workspace/pick-data?tab=trends&v=25&page=2",
  );
  const url = new URL(href, "https://example.org");
  expect(url.searchParams.get("title")).toBe("雨夜");
  expect(safeEditingReturn(url.searchParams.get("returnTo"))).toBe(
    "/workspace/pick-data?tab=trends&v=25&page=2",
  );
  expect(safeEditingReturn("//evil.example/path")).toBeNull();
  expect(safeEditingReturn("/workspace/pick-data/../../evil")).toBeNull();
});
it("resolves a real task tool envelope without inventing a task from narration", () => {
  expect(editingTaskId(JSON.stringify({ task: { id: "t-1" } }))).toBe("t-1");
  expect(editingTaskId("已完成任务 t-2")).toBeNull();
});
