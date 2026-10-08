import { afterEach, expect, it, rs } from "@rstest/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";

import { EditingTaskView } from "@/components/workspace/editing/editing-task";
import { AuthProvider, type User } from "@/core/auth/AuthProvider";
import type { EditingTask } from "@/core/editing/types";
rs.mock("next/navigation", () => ({
  useRouter: () => ({ push: rs.fn() }),
  usePathname: () => "/workspace/editing/t",
  useSearchParams: () => new URLSearchParams(),
}));
afterEach(() => {
  cleanup();
  rs.restoreAllMocks();
});
const task = {
  id: "t",
  updated_at: "2026-10-08",
  result: "partial",
  plan_confirmed: true,
  title: "雨夜",
  requirements: {
    profile: "hook",
    instructions: "冲突",
    output_count: 3,
    duration_seconds: 30,
    aspect_ratio: "9:16",
    language: "auto",
    review_plan: false,
  },
  device_id: "mac",
  source_thread_id: null,
  parent_task_id: null,
  created_at: "2026-10-08",
  status: "partial",
  stage: "rendering",
  source_manifest: null,
  source_directory: null,
  manifest_frozen: true,
  outputs: [
    {
      id: "out-1",
      status: "completed",
      error: null,
      result: {
        verified: true,
        artifact_id: "a",
        size_bytes: 3,
        duration_seconds: 30,
      },
      access_status: "device_offline",
    },
    {
      id: "out-2",
      status: "completed",
      error: null,
      result: {
        verified: true,
        artifact_id: "a",
        size_bytes: 3,
        duration_seconds: 30,
      },
      access_status: "device_offline",
    },
    {
      id: "out-3",
      status: "failed",
      result: null,
      error: "render_failed",
      access_status: "device_offline",
    },
  ],
  requested_count: 3,
  completed_count: 2,
  preparation_reasons: [],
  device_status: "offline",
  access_status: "device_offline",
  available_actions: ["retry"],
  plan: null,
} as EditingTask;
it("preserves partial delivery while offline and retries only the failed output", async () => {
  const writes: unknown[] = [];
  rs.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    if (
      (typeof input === "string"
        ? input
        : input instanceof URL
          ? input.href
          : input.url
      ).endsWith("/retry")
    ) {
      writes.push(JSON.parse(init?.body as string));
      return Response.json({ ...task, status: "queued" });
    }
    return Response.json(task);
  });
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <AuthProvider initialUser={{ id: "owner" } as User}>
      <QueryClientProvider client={client}>
        <EditingTaskView taskId="t" />
      </QueryClientProvider>
    </AuthProvider>,
  );
  await waitFor(() =>
    expect(screen.getByRole("status").textContent).toContain(
      "部分完成 · 2/3 条",
    ),
  );
  expect(screen.queryByText("来源对话")).toBeNull();
  expect(screen.queryByRole("link", { name: "下载成片" })).toBeNull();
  expect(screen.queryByRole("button", { name: "预览成片" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "重试此条" }));
  await waitFor(() => expect(writes).toHaveLength(1));
  expect(writes[0]).toMatchObject({ output_ids: ["out-3"] });
  client.clear();
});

it("shows stop acknowledgement as pending until the worker confirms", async () => {
  rs.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const url =
      typeof input === "string"
        ? input
        : input instanceof URL
          ? input.href
          : input.url;
    return Response.json({
      ...task,
      status: url.endsWith("/stop") ? "stopping" : "running",
      available_actions: url.endsWith("/stop") ? [] : ["stop"],
    });
  });
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <AuthProvider initialUser={{ id: "stop-owner" } as User}>
      <QueryClientProvider client={client}>
        <EditingTaskView taskId="t" />
      </QueryClientProvider>
    </AuthProvider>,
  );
  fireEvent.click(await screen.findByRole("button", { name: "停止剪辑" }));
  await screen.findByText(/已请求停止，等待设备确认/);
  expect(screen.queryByText(/^已停止/)).toBeNull();
  expect(screen.queryByRole("button", { name: "重试此条" })).toBeNull();
  client.clear();
});

it("separates missing output files from a completed task and offers access retry", async () => {
  rs.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const url =
      typeof input === "string"
        ? input
        : input instanceof URL
          ? input.href
          : input.url;
    return Response.json(
      url.endsWith("/access")
        ? { access_status: "file_missing" }
        : {
            ...task,
            status: "completed",
            device_status: "online",
            completed_count: 3,
            outputs: [task.outputs[0]],
            available_actions: [],
          },
    );
  });
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <AuthProvider initialUser={{ id: "file-owner" } as User}>
      <QueryClientProvider client={client}>
        <EditingTaskView taskId="t" />
      </QueryClientProvider>
    </AuthProvider>,
  );
  fireEvent.click(await screen.findByRole("button", { name: "检查成片访问" }));
  await screen.findByText(/生成设备未找到原成片/);
  expect(screen.getByRole("status").textContent).toContain("已完成 · 3/3 条");
  expect(screen.getByRole("button", { name: "重新检查文件" })).toBeTruthy();
  expect(screen.queryByRole("link", { name: "下载成片" })).toBeNull();
  client.clear();
});

it("keeps a partly verified selection pending and preserves its receiving directory", async () => {
  const writes: unknown[] = [];
  rs.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url =
      typeof input === "string"
        ? input
        : input instanceof URL
          ? input.href
          : input.url;
    if (init?.method === "POST") writes.push(JSON.parse(init.body as string));
    if (url.endsWith("/devices"))
      return Response.json({
        items: [
          {
            id: "mac",
            name: "Studio",
            ready: true,
            online: true,
            grants: ["incoming"],
            reasons: [],
          },
        ],
      });
    if (url.endsWith("/capabilities")) return Response.json({ profiles: [] });
    return Response.json({
      ...task,
      status: "waiting",
      manifest_frozen: false,
      outputs: [],
      source_manifest: {
        version: 2,
        grant_id: "incoming",
        files: [
          {
            media_id: "a",
            name: "第一集.mp4",
            episode: 1,
            relative_path: "第一集.mp4",
            size_bytes: 100,
            state: "verified",
            sha256: "a".repeat(64),
            duration_seconds: 30,
          },
          {
            media_id: "b",
            name: "第二集.mp4",
            episode: 2,
            relative_path: "第二集.mp4",
            size_bytes: 100,
            state: "received",
          },
        ],
      },
    });
  });
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <AuthProvider initialUser={{ id: "prepare-owner" } as User}>
      <QueryClientProvider client={client}>
        <EditingTaskView taskId="t" />
      </QueryClientProvider>
    </AuthProvider>,
  );
  await screen.findByText(
    "本次所选素材：1/2 个已在 Mac 校验，未完成项仍保留在清单中。",
  );
  await waitFor(() =>
    expect(screen.getByLabelText<HTMLSelectElement>("已授权目录").value).toBe(
      "incoming",
    ),
  );
  expect(writes).toHaveLength(0);
  client.clear();
});

it.each([true, false])(
  "retries the stopped scope without replacing delivered outputs (confirmed plan: %s)",
  async (confirmedPlan) => {
    const writes: unknown[] = [];
    const stopped = {
      ...task,
      status: "stopped",
      stage: "stopped",
      plan_confirmed: confirmedPlan,
      completed_count: confirmedPlan ? 2 : 0,
      plan: confirmedPlan ? { outputs: [] } : null,
      outputs: confirmedPlan
        ? [
            ...task.outputs.slice(0, 2),
            { ...task.outputs[2], status: "stopped", error: null },
          ]
        : task.outputs.map((output) => ({
            ...output,
            status: "stopped",
            result: null,
            error: null,
          })),
    };
    rs.spyOn(globalThis, "fetch").mockImplementation(async (_input, init) => {
      if (init?.method === "POST") writes.push(JSON.parse(init.body as string));
      return Response.json(stopped);
    });
    const client = new QueryClient();
    render(
      <AuthProvider initialUser={{ id: "stopped-owner" } as User}>
        <QueryClientProvider client={client}>
          <EditingTaskView taskId="t" />
        </QueryClientProvider>
      </AuthProvider>,
    );
    fireEvent.click(
      await screen.findByRole("button", {
        name: confirmedPlan ? "重试此条" : "重新处理已停止任务",
      }),
    );
    await waitFor(() => expect(writes).toHaveLength(1));
    expect(screen.queryByText(/执行状态待确认/)).toBeNull();
    expect(writes[0]).toMatchObject(
      confirmedPlan ? { output_ids: ["out-3"] } : { stage: "transcribing" },
    );
    client.clear();
  },
);
