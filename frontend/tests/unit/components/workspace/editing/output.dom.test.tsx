import { afterEach, expect, it, rs } from "@rstest/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";

import { EditingOutput } from "@/components/workspace/editing/editing-output";
import { AuthProvider, type User } from "@/core/auth/AuthProvider";
import type { EditingTask, Output } from "@/core/editing/types";

rs.mock("next/navigation", () => ({
  useRouter: () => ({ push: rs.fn() }),
  usePathname: () => "/workspace/editing/t",
}));
afterEach(() => {
  cleanup();
  rs.restoreAllMocks();
});
const output = {
  id: "out-1",
  status: "completed",
  result: { verified: true },
  error: null,
} as Output;
it("checks only a selected output and requires fresh access after reconnect", async () => {
  const probes: string[] = [];
  rs.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    probes.push(
      typeof input === "string"
        ? input
        : input instanceof URL
          ? input.href
          : input.url,
    );
    return Response.json({ access_status: "available" });
  });
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const ui = (device_status: string) => (
    <AuthProvider initialUser={{ id: "owner" } as User}>
      <QueryClientProvider client={client}>
        <ul>
          {[output, { ...output, id: "out-2" }].map((item) => (
            <EditingOutput
              key={item.id}
              task={
                {
                  id: "t",
                  device_status,
                  access_status: "device_offline",
                } as EditingTask
              }
              output={item}
              busy={false}
              onRetry={() => undefined}
            />
          ))}
        </ul>
      </QueryClientProvider>
    </AuthProvider>
  );
  const view = render(ui("online"));
  expect(probes).toHaveLength(0);
  fireEvent.click(screen.getAllByRole("button", { name: "检查成片访问" })[0]!);
  await screen.findByRole("link", { name: "下载成片" });
  expect(probes).toHaveLength(1);
  expect(probes[0]).toContain("out-1/access");
  view.rerender(ui("offline"));
  expect(screen.queryByRole("link", { name: "下载成片" })).toBeNull();
  view.rerender(ui("online"));
  expect(screen.queryByRole("link", { name: "下载成片" })).toBeNull();
  fireEvent.click(screen.getAllByRole("button", { name: "检查成片访问" })[0]!);
  await screen.findByRole("link", { name: "下载成片" });
  expect(probes).toHaveLength(2);
  client.clear();
});

it("keeps current playback mounted while its background access check is pending", async () => {
  let completeProbe: ((response: Response) => void) | undefined;
  let reads = 0;
  rs.spyOn(globalThis, "fetch").mockImplementation(async () => {
    if (++reads === 1) return Response.json({ access_status: "available" });
    return new Promise<Response>((resolve) => {
      completeProbe = resolve;
    });
  });
  const client = new QueryClient();
  const view = render(
    <AuthProvider initialUser={{ id: "owner" } as User}>
      <QueryClientProvider client={client}>
        <ul>
          <EditingOutput
            task={{ id: "t", device_status: "online" } as EditingTask}
            output={output}
            busy={false}
            onRetry={() => undefined}
          />
        </ul>
      </QueryClientProvider>
    </AuthProvider>,
  );
  fireEvent.click(screen.getByRole("button", { name: "检查成片访问" }));
  fireEvent.click(await screen.findByRole("button", { name: "预览成片" }));
  const video = screen.getByLabelText<HTMLVideoElement>("成片 out-1 预览");
  video.currentTime = 12;
  void client.refetchQueries();
  await waitFor(() => expect(reads).toBe(2));
  expect(screen.getByLabelText("成片 out-1 预览")).toBe(video);
  expect(video.currentTime).toBe(12);
  completeProbe!(Response.json({ access_status: "file_missing" }));
  await waitFor(() =>
    expect(screen.queryByLabelText("成片 out-1 预览")).toBeNull(),
  );
  view.unmount();
  client.clear();
});
