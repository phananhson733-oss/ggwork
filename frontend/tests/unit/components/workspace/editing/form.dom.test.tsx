import { afterEach, expect, it, rs } from "@rstest/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";

import { EditingForm } from "@/components/workspace/editing/editing-form";
import { AuthProvider, type User } from "@/core/auth/AuthProvider";
rs.mock("next/navigation", () => ({
  useRouter: () => ({ push: rs.fn() }),
  usePathname: () => "/workspace/editing/new",
  useSearchParams: () => new URLSearchParams(),
}));
afterEach(() => {
  cleanup();
  rs.restoreAllMocks();
});
it("holds a directory draft until Start and creates one standalone request", async () => {
  const submissions: unknown[] = [];
  rs.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url =
      typeof input === "string"
        ? input
        : input instanceof URL
          ? input.href
          : input.url;
    if (url.endsWith("/devices"))
      return Response.json({
        items: [
          {
            id: "mac",
            name: "Studio",
            online: true,
            ready: true,
            revoked: false,
            grants: ["drama"],
            reasons: [],
          },
        ],
      });
    if (url.endsWith("/capabilities"))
      return Response.json({
        profiles: [{ id: "hook", available: true, reasons: [] }],
        skill_enabled: true,
        limits: {
          max_sources: 50,
          max_outputs: 4,
          min_duration_seconds: 5,
          max_duration_seconds: 90,
        },
      });
    if (url.endsWith("/tasks")) {
      submissions.push(JSON.parse(init?.body as string));
      return Response.json({ id: "task" });
    }
    return Response.json({ models: [] });
  });
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <AuthProvider initialUser={{ id: "owner" } as User}>
      <QueryClientProvider client={client}>
        <EditingForm initialTitle="雨夜" />
      </QueryClientProvider>
    </AuthProvider>,
  );
  await screen.findByRole("option", { name: /Studio/ });
  fireEvent.change(screen.getByLabelText("选择执行设备"), {
    target: { value: "mac" },
  });
  fireEvent.change(screen.getByLabelText("已授权目录"), {
    target: { value: "drama" },
  });
  fireEvent.change(screen.getByLabelText("剪辑要求"), {
    target: { value: "开场保留冲突" },
  });
  expect(submissions).toHaveLength(0);
  fireEvent.click(screen.getByRole("button", { name: "开始剪辑" }));
  await waitFor(() => expect(submissions).toHaveLength(1));
  expect(submissions[0]).toMatchObject({
    title: "雨夜",
    device_id: "mac",
    source_directory: { grant_id: "drama", relative_path: "." },
  });
  expect(submissions[0]).not.toHaveProperty("source_thread_id");
  client.clear();
});

it("confirms the same uncertain submission after reopening instead of creating a new request", async () => {
  const submissions: { request_id: string }[] = [];
  rs.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url =
      typeof input === "string"
        ? input
        : input instanceof URL
          ? input.href
          : input.url;
    if (url.endsWith("/devices")) return Response.json({ items: [] });
    if (url.endsWith("/capabilities"))
      return Response.json({
        profiles: [{ id: "hook", available: true, reasons: [] }],
        skill_enabled: true,
        limits: {
          max_sources: 50,
          max_outputs: 4,
          min_duration_seconds: 5,
          max_duration_seconds: 90,
        },
      });
    if (url.endsWith("/tasks")) {
      submissions.push(
        JSON.parse(init?.body as string) as { request_id: string },
      );
      throw new TypeError("network lost after acceptance");
    }
    return Response.json({ models: [] });
  });
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const ui = (
    <AuthProvider initialUser={{ id: "restore-owner" } as User}>
      <QueryClientProvider client={client}>
        <EditingForm initialTitle="雨夜" />
      </QueryClientProvider>
    </AuthProvider>
  );
  const first = render(ui);
  await screen.findByRole("option", { name: "Hook 剪辑" });
  fireEvent.change(screen.getByLabelText("剪辑要求"), {
    target: { value: "保留冲突" },
  });
  fireEvent.click(screen.getByRole("button", { name: "开始剪辑" }));
  await screen.findByText(/提交结果尚未确认/);
  first.unmount();
  render(ui);
  fireEvent.click(await screen.findByRole("button", { name: "确认提交结果" }));
  await waitFor(() => expect(submissions).toHaveLength(2));
  expect(submissions[1]?.request_id).toBe(submissions[0]?.request_id);
  client.clear();
});
