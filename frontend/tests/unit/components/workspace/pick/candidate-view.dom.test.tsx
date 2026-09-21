import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

import { CandidateView } from "@/components/workspace/pick/candidate-view";
import type { PickResult } from "@/core/pick/types";

afterEach(cleanup);
const result: PickResult = {
  id: "r1",
  thread_id: "t1",
  run_id: "run",
  run_status: "success",
  catalog_batch_id: "b1",
  knowledge_batch_id: null,
  rule_version: "v1",
  ranking_version: "v1",
  created_at: "2026-09-21T00:00:00Z",
  conditions: { limit: 5, exclude_selected: true, language: "en" },
  items: [
    {
      item_id: "i1",
      identity: "source/1",
      title: "样例剧",
      theater: "Example",
      language: "en",
      availability: "unknown",
      reason: "符合条件",
      warnings: ["状态待核实"],
      evidence: [],
    },
  ],
};
describe("candidate interaction", () => {
  it.each([
    "pending",
    "running",
    "interrupted",
    "error",
    "timeout",
    "unknown",
  ] as const)("blocks saving a %s run", (run_status) => {
    const save = rs.fn();
    render(
      <CandidateView
        result={{ ...result, run_status }}
        selected={["i1"]}
        onToggle={rs.fn()}
        onSave={save}
        busy={false}
      />,
    );
    expect(screen.getByRole("status")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "保存选中（1）" }));
    expect(save).not.toHaveBeenCalled();
  });
  it("shows historical evidence without save controls in read-only mode", () => {
    render(
      <CandidateView
        result={result}
        selected={[]}
        onToggle={rs.fn()}
        onSave={rs.fn()}
        busy={false}
        readOnly
      />,
    );
    expect(screen.queryByRole("checkbox")).toBeNull();
    expect(screen.queryByRole("button", { name: /保存选中/ })).toBeNull();
  });
  it("shows a real shortfall and does not save without a user click", () => {
    const save = rs.fn();
    render(
      <CandidateView
        result={result}
        selected={[]}
        onToggle={rs.fn()}
        onSave={save}
        busy={false}
      />,
    );
    expect(screen.getByText("找到 1 部 / 请求 5 部")).toBeTruthy();
    expect(screen.getByText("状态待核实")).toBeTruthy();
    expect(
      screen
        .getByRole("button", {
          name: "保存选中（0）",
        })
        .hasAttribute("disabled"),
    ).toBe(true);
    expect(save).not.toHaveBeenCalled();
  });
  it("uses immutable IDs for checkbox changes and save", () => {
    const toggle = rs.fn();
    const save = rs.fn();
    render(
      <CandidateView
        result={result}
        selected={["i1"]}
        onToggle={toggle}
        onSave={save}
        busy={false}
      />,
    );
    fireEvent.click(screen.getByRole("checkbox", { name: "选择样例剧" }));
    expect(toggle).toHaveBeenCalledWith("i1");
    fireEvent.click(screen.getByRole("button", { name: "保存选中（1）" }));
    expect(save).toHaveBeenCalledTimes(1);
  });
});
