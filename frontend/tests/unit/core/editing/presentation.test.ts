import { expect, it } from "@rstest/core";

import {
  editingCreateHref,
  editingLabel,
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

it("labels the preparation and completed stages without exposing unknown diagnostics", () => {
  expect(editingLabel("preparing")).toBe("准备素材");
  expect(editingLabel("finished")).toBe("处理已结束");
  expect(editingLabel("unknown /private/file")).toBe(
    "状态暂不可识别，请重新检查；如仍未恢复，请联系管理员",
  );
});
it("explains missing native upload receipts without showing the internal code", () => {
  expect(editingLabel("source_receipt_missing")).toBe(
    "这些素材尚未完成接收，请继续上传或重新提交未完成的文件",
  );
});
it("explains a long-dialogue refusal with an explicit new-selection recovery", () => {
  expect(editingLabel("planner_input_too_large")).toBe(
    "本次选集的对白内容过长，请新建任务并减少选集；原任务和素材会保留",
  );
});
