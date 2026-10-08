import { afterEach, expect, it, rs } from "@rstest/core";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

import { PlanConflictReview } from "@/components/workspace/pick/plans/plan-conflict";
import { planSchema } from "@/core/pick/completion-types";
import { editablePlan } from "@/core/pick/plan-draft";

import fixture from "../../../core/pick/fixtures/completion-v1.json";

afterEach(cleanup);
const select = (label: string, value: string) =>
  fireEvent.change(screen.getByLabelText(label), { target: { value } });

it("submits the displayed preserved instant for a retained server-added row", () => {
  const base = planSchema.parse(fixture.plan);
  base.rows = [];
  const remote = planSchema.parse(fixture.plan);
  remote.version = 2;
  remote.rows[0]!.local_time = "2026-11-01T01:30";
  remote.rows[0]!.fold = 1;
  remote.rows[0]!.scheduled_at = "2026-11-01T07:30:00+00:00";
  const local = { ...editablePlan(base), timezone: "Asia/Shanghai" };
  const apply = rs.fn();
  render(
    <PlanConflictReview
      base={base}
      local={local}
      server={remote}
      onApply={apply}
    />,
  );
  select("计划时区：保留版本", "local");
  select("行顺序：保留版本", "server");
  select(`${remote.rows[0]!.row_id} · 行保留状态：保留版本`, "server");
  select("时区冲突处理", "keep_instant");
  expect(screen.getByText(/转换后当地时间：2026-11-01T15:30/)).toBeTruthy();
  fireEvent.click(
    screen.getByRole("button", { name: "应用所选字段，以新版本继续" }),
  );
  expect(apply.mock.calls[0]![0].rows[0]).toMatchObject({
    local_time: "2026-11-01T15:30",
    fold: null,
  });
});

it.each([null, "2026-11-02T12:00"])(
  "requires explicit clearing of an incompatible retained fold at %s",
  (time) => {
    const base = planSchema.parse(fixture.plan);
    const remote = planSchema.parse(fixture.plan);
    remote.rows[0]!.local_time = "2026-11-01T01:30";
    remote.rows[0]!.fold = 1;
    const local = editablePlan(base);
    local.rows[0]!.local_time = time;
    local.rows[0]!.fold = null;
    const id = local.rows[0]!.row_id;
    const apply = rs.fn();
    render(
      <PlanConflictReview
        base={base}
        local={local}
        server={remote}
        onApply={apply}
      />,
    );
    select(`${id} · 当地时间：保留版本`, "local");
    select(`${id} · 重复时刻偏移：保留版本`, "server");
    const button = screen.getByRole<HTMLButtonElement>("button", {
      name: "应用所选字段，以新版本继续",
    });
    expect(button.disabled).toBe(true);
    fireEvent.click(
      screen.getByRole("button", { name: `${id} · 清除不适用的偏移` }),
    );
    expect(button.disabled).toBe(false);
    fireEvent.click(button);
    expect(apply.mock.calls[0]![0].rows[0]).toMatchObject({
      local_time: time,
      fold: null,
    });
  },
);
