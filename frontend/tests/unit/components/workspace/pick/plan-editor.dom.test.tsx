import { afterEach, expect, it, rs } from "@rstest/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  cleanup,
  fireEvent,
  render as testingRender,
  screen,
} from "@testing-library/react";
import { type ReactElement } from "react";

rs.mock("@/core/auth/AuthProvider", () => ({
  useAuth: () => ({ user: { id: "owner-a" } }),
}));
rs.mock("@/core/api/fetcher", () => ({ fetch: rs.fn() }));
rs.mock("@/core/config", () => ({ getBackendBaseURL: () => "" }));
import { PlanEditor } from "@/components/workspace/pick/plans/plan-editor";
import { fetch as fetcher } from "@/core/api/fetcher";
import { planSchema } from "@/core/pick/completion-types";

import fixture from "../../../core/pick/fixtures/completion-v1.json";
const render = (ui: ReactElement) =>
  testingRender(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      {ui}
    </QueryClientProvider>,
  );
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
it("offers stay, discard and save-and-continue before leaving dirty plan", () => {
  render(<PlanEditor initial={planSchema.parse(fixture.plan)} />);
  fireEvent.change(screen.getByLabelText("计划名称"), {
    target: { value: "未保存" },
  });
  fireEvent.click(screen.getByRole("link", { name: "全部排期" }));
  expect(screen.getByRole("dialog")).toBeTruthy();
  expect(screen.getByRole("button", { name: "保存并继续" })).toBeTruthy();
  expect(screen.getByRole("button", { name: "丢弃修改并离开" })).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "留在本页" }));
  expect(screen.getByLabelText<HTMLInputElement>("计划名称").value).toBe(
    "未保存",
  );
});
it("requires saving a keep-instant timezone conversion before changing its local time", () => {
  const original = planSchema.parse(fixture.plan);
  render(<PlanEditor initial={original} />);
  fireEvent.change(screen.getByLabelText("新时区（IANA）"), {
    target: { value: "America/New_York" },
  });
  fireEvent.change(screen.getByLabelText("时间保留方式"), {
    target: { value: "keep_instant" },
  });
  fireEvent.click(screen.getByRole("button", { name: "预览时区变化" }));
  fireEvent.click(screen.getByRole("button", { name: "确认时区变化" }));
  fireEvent.click(
    screen.getByRole("button", { name: `编辑 ${original.rows[0]!.title}` }),
  );
  expect(
    screen.getByLabelText<HTMLInputElement>("当地发布时间（America/New_York）")
      .disabled,
  ).toBe(true);
  expect(
    screen.getByText("请先保存保留同一时刻的时区变更，再编辑当地时间。"),
  ).toBeTruthy();
});
it("invalidates timezone preview when a later row edit would otherwise be overwritten", () => {
  const original = planSchema.parse(fixture.plan);
  render(<PlanEditor initial={original} />);
  fireEvent.change(screen.getByLabelText("新时区（IANA）"), {
    target: { value: "America/New_York" },
  });
  fireEvent.change(screen.getByLabelText("时间保留方式"), {
    target: { value: "keep_instant" },
  });
  fireEvent.click(screen.getByRole("button", { name: "预览时区变化" }));
  fireEvent.click(
    screen.getByRole("button", { name: `编辑 ${original.rows[0]!.title}` }),
  );
  fireEvent.change(screen.getByLabelText("个人备注"), {
    target: { value: "保留我的新输入" },
  });
  expect(screen.queryByRole("button", { name: "确认时区变化" })).toBeNull();
  expect(screen.getByLabelText<HTMLTextAreaElement>("个人备注").value).toBe(
    "保留我的新输入",
  );
});
it("reconciles a server timezone conflict with an explicit choice without losing local notes", async () => {
  const original = planSchema.parse(fixture.plan);
  const remote = { ...original, version: 2, timezone: "America/New_York" };
  rs.mocked(fetcher)
    .mockResolvedValueOnce(
      Response.json(
        {
          detail: {
            code: "version_conflict",
            message: "版本变化",
            retryable: false,
            current_version: 2,
          },
        },
        { status: 409 },
      ),
    )
    .mockResolvedValueOnce(Response.json(remote))
    .mockResolvedValueOnce(
      Response.json({
        ...original,
        version: 3,
        rows: original.rows.map((row) => ({ ...row, note: "本地备注" })),
      }),
    );
  render(<PlanEditor initial={original} />);
  fireEvent.click(
    screen.getByRole("button", { name: `编辑 ${original.rows[0]!.title}` }),
  );
  fireEvent.change(screen.getByLabelText("个人备注"), {
    target: { value: "本地备注" },
  });
  fireEvent.click(screen.getByRole("button", { name: "保存计划" }));
  await screen.findByLabelText("时区冲突处理");
  expect(
    screen.getByRole<HTMLButtonElement>("button", {
      name: "保留本地修改，以新版本继续",
    }).disabled,
  ).toBe(true);
  fireEvent.change(screen.getByLabelText("时区冲突处理"), {
    target: { value: "keep_local_time" },
  });
  fireEvent.click(
    screen.getByRole("button", { name: "保留本地修改，以新版本继续" }),
  );
  fireEvent.click(screen.getByRole("button", { name: "保存计划" }));
  await screen.findByText("已保存版本 3");
  const sent = JSON.parse(
    rs.mocked(fetcher).mock.calls[2]?.[1]?.body as string,
  );
  expect(sent).toMatchObject({
    expected_version: 2,
    timezone: "America/Chicago",
    timezone_change: "keep_local_time",
    rows: [{ note: "本地备注" }],
  });
});
