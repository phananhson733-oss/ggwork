import { afterEach, expect, it, rs } from "@rstest/core";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

rs.mock("@/core/api/fetcher", () => ({ fetch: rs.fn() }));
rs.mock("@/core/config", () => ({ getBackendBaseURL: () => "" }));
import { PlanEditor } from "@/components/workspace/pick/plans/plan-editor";
import { fetch as fetcher } from "@/core/api/fetcher";
import { planSchema } from "@/core/pick/completion-types";

import fixture from "../../../core/pick/fixtures/completion-v1.json";
afterEach(() => {
  cleanup();
  rs.mocked(fetcher).mockReset();
});
it("retains local title after a version conflict and compares the current server revision", async () => {
  const original = planSchema.parse(fixture.plan);
  const current = {
    ...original,
    title: "另一处修改",
    version: original.version + 1,
  };
  rs.mocked(fetcher)
    .mockResolvedValueOnce(
      Response.json(
        {
          detail: {
            code: "version_conflict",
            message: "版本已更新",
            retryable: false,
            current_version: current.version,
          },
        },
        { status: 409 },
      ),
    )
    .mockResolvedValueOnce(Response.json(current));
  render(<PlanEditor initial={original} />);
  fireEvent.change(screen.getByLabelText("计划名称"), {
    target: { value: "我的未保存修改" },
  });
  fireEvent.click(screen.getByRole("button", { name: "保存计划" }));
  await screen.findByText("另一处修改");
  expect(screen.getByLabelText<HTMLInputElement>("计划名称").value).toBe(
    "我的未保存修改",
  );
  expect(
    screen.getByRole("button", { name: "保留本地修改，以新版本继续" }),
  ).toBeTruthy();
});
it("a mandatory blocked row prevents execution export and links to the row", async () => {
  const original = planSchema.parse(fixture.plan);
  rs.mocked(fetcher).mockResolvedValueOnce(
    Response.json({
      ...fixture.preview,
      plan: original,
      exportable: false,
      checks: [
        {
          row_id: original.rows[0]!.row_id,
          status: "blocked",
          blockers: ["渠道许可未知"],
          warnings: [],
          current_pin: null,
        },
      ],
    }),
  );
  render(<PlanEditor initial={original} />);
  fireEvent.click(screen.getByRole("button", { name: "核对执行条件" }));
  await screen.findByText("渠道许可未知");
  expect(
    screen.queryByRole("button", { name: /确认版本.*生成执行表/ }),
  ).toBeNull();
  expect(screen.getByRole("link", { name: /定位/ }).getAttribute("href")).toBe(
    `#row-${original.rows[0]!.row_id}`,
  );
});
it("retries unchanged edits with the same command after an unknown write result", async () => {
  const original = planSchema.parse(fixture.plan);
  rs.mocked(fetcher).mockRejectedValueOnce(new Error("response lost"));
  rs.mocked(fetcher).mockResolvedValueOnce(
    Response.json({
      ...original,
      title: "重试标题",
      version: original.version + 1,
    }),
  );
  render(<PlanEditor initial={original} />);
  fireEvent.change(screen.getByLabelText("计划名称"), {
    target: { value: "重试标题" },
  });
  fireEvent.click(screen.getByRole("button", { name: "保存计划" }));
  await screen.findByText("操作结果尚未确认，请保留修改并重试原操作。");
  fireEvent.click(screen.getByRole("button", { name: "保存计划" }));
  await screen.findByText(`已保存版本 ${original.version + 1}`);
  expect(rs.mocked(fetcher).mock.calls[0]?.[1]?.body).toBe(
    rs.mocked(fetcher).mock.calls[1]?.[1]?.body,
  );
});
it("editing after preview removes the previous version confirmation", async () => {
  const original = planSchema.parse(fixture.plan);
  rs.mocked(fetcher).mockResolvedValueOnce(
    Response.json({
      ...fixture.preview,
      plan: original,
      exportable: true,
      checks: original.rows.map((row) => ({
        row_id: row.row_id,
        status: "ready",
        blockers: [],
        warnings: [],
        current_pin: row.source_pin,
      })),
    }),
  );
  render(<PlanEditor initial={original} />);
  fireEvent.click(screen.getByRole("button", { name: "核对执行条件" }));
  await screen.findByRole("button", { name: /确认版本.*生成执行表/ });
  fireEvent.change(screen.getByLabelText("计划名称"), {
    target: { value: "已改标题" },
  });
  expect(
    screen.queryByRole("button", { name: /确认版本.*生成执行表/ }),
  ).toBeNull();
});
it("keeps an immutable export receipt when downloading fails and retries that same file", async () => {
  const original = planSchema.parse(fixture.plan);
  const preview = {
    ...fixture.preview,
    plan: original,
    exportable: true,
    checks: original.rows.map((row) => ({
      row_id: row.row_id,
      status: "ready",
      blockers: [],
      warnings: [],
      current_pin: row.source_pin,
    })),
  };
  const exported = {
    ...fixture.export,
    plan_id: original.id,
    plan_version: original.version,
    preview_id: preview.preview_id,
  };
  rs.mocked(fetcher)
    .mockResolvedValueOnce(Response.json(preview))
    .mockResolvedValueOnce(Response.json(exported))
    .mockRejectedValueOnce(new Error("download failed"))
    .mockRejectedValueOnce(new Error("download failed"));
  render(<PlanEditor initial={original} />);
  fireEvent.click(screen.getByRole("button", { name: "核对执行条件" }));
  fireEvent.click(
    await screen.findByRole("button", { name: /确认版本.*生成执行表/ }),
  );
  fireEvent.click(
    await screen.findByRole("button", { name: "下载同一执行表" }),
  );
  await screen.findByText("操作结果尚未确认，请保留修改并重试原操作。");
  fireEvent.click(screen.getByRole("button", { name: "下载同一执行表" }));
  await screen.findByText("操作结果尚未确认，请保留修改并重试原操作。");
  expect(rs.mocked(fetcher).mock.calls[2]?.[0]).toBe(
    `/api/pick/exports/${exported.id}`,
  );
  expect(rs.mocked(fetcher).mock.calls[3]?.[0]).toBe(
    `/api/pick/exports/${exported.id}`,
  );
  expect(
    screen.getByText(`执行表已生成 · 版本 ${original.version}`),
  ).toBeTruthy();
});
