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
import { EditingPrepare } from "@/components/workspace/editing/editing-prepare";
import { AuthProvider, type User } from "@/core/auth/AuthProvider";
import type { CreateTask, EditingTask, Manifest } from "@/core/editing/types";

rs.mock("next/navigation", () => ({
  useRouter: () => ({ push: rs.fn() }),
  usePathname: () => "/workspace/editing/new",
}));
afterEach(() => {
  cleanup();
  sessionStorage.clear();
  rs.restoreAllMocks();
});
function mockAPI(write: (url: string, body: CreateTask) => Response) {
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
            ready: true,
            online: true,
            grants: ["incoming"],
            reasons: [],
          },
        ],
      });
    if (url.endsWith("/capabilities"))
      return Response.json({
        profiles: [{ id: "hook", available: true, reasons: [] }],
        limits: {
          max_sources: 50,
          max_outputs: 4,
          min_duration_seconds: 5,
          max_duration_seconds: 90,
        },
      });
    if (url.endsWith("/uploads"))
      return Response.json({
        transfer_id: "transfer",
        offset: 0,
        chunk_bytes: 100,
      });
    if (url.includes("/uploads/"))
      return Response.json({ transfer_id: "transfer", offset: 3 });
    return write(url, JSON.parse(init?.body as string) as CreateTask);
  });
}
function mount(child: React.ReactNode) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const view = render(
    <AuthProvider initialUser={{ id: "upload-owner" } as User}>
      <QueryClientProvider client={client}>{child}</QueryClientProvider>
    </AuthProvider>,
  );
  return {
    close: () => {
      view.unmount();
      client.clear();
    },
  };
}
async function fillNewFile(name: string) {
  await screen.findByRole("option", { name: /Studio/ });
  fireEvent.change(screen.getByLabelText("选择执行设备"), {
    target: { value: "mac" },
  });
  fireEvent.change(screen.getByLabelText("提供素材方式"), {
    target: { value: "files" },
  });
  fireEvent.change(screen.getByLabelText("已授权目录"), {
    target: { value: "incoming" },
  });
  fireEvent.change(screen.getByLabelText("剪辑要求"), {
    target: { value: "保留冲突" },
  });
  fireEvent.change(screen.getByLabelText("选择本次素材"), {
    target: { files: [new File(["abc"], name, { type: "video/mp4" })] },
  });
  fireEvent.change(screen.getByLabelText(`${name} 集号`), {
    target: { value: "4" },
  });
}
it.each([
  ["同名剧集.MP4", ".MP4"],
  ["同名剧集.mkv", ".mkv"],
  ["同名剧集.mp4.exe", ""],
])(
  "isolates repeated same-name uploads of %s while preserving display metadata",
  async (name, suffix) => {
    const requests: CreateTask[] = [];
    mockAPI((_url, body) => {
      requests.push(body);
      return Response.json({ id: `task-${requests.length}` });
    });
    for (let n = 0; n < 2; n++) {
      const view = mount(<EditingForm initialTitle="雨夜" />);
      await fillNewFile(name);
      fireEvent.click(screen.getByRole("button", { name: "开始剪辑" }));
      await waitFor(() => expect(requests).toHaveLength(n + 1));
      await waitFor(() =>
        expect(
          sessionStorage.getItem("editing:pending:upload-owner:new"),
        ).toBeNull(),
      );
      view.close();
    }
    const sources = requests.map(
      (request) => request.source_manifest!.files[0]!,
    );
    for (const source of sources) {
      expect(source).toMatchObject({
        name,
        episode: 4,
        state: "selected",
        relative_path: `${source.media_id}${suffix}`,
      });
      expect(source.relative_path).toMatch(
        /^[0-9a-f-]{36}(\.(mp4|mov|mkv|m4v|webm))?$/i,
      );
      expect(source.relative_path.length).toBeLessThanOrEqual(41);
    }
    expect(sources[0]!.relative_path).not.toBe(sources[1]!.relative_path);
  },
);
it("retains the same receiving path and request when confirming an uncertain upload intent after reopening", async () => {
  const requests: CreateTask[] = [];
  mockAPI((_url, body) => {
    requests.push(body);
    throw new TypeError("uncertain request");
  });
  const first = mount(<EditingForm initialTitle="雨夜" />);
  await fillNewFile("第一集.mp4");
  fireEvent.click(screen.getByRole("button", { name: "开始剪辑" }));
  await screen.findByText(/提交结果尚未确认/);
  first.close();
  const second = mount(<EditingForm initialTitle="雨夜" />);
  fireEvent.click(await screen.findByRole("button", { name: "确认提交结果" }));
  await waitFor(() => expect(requests).toHaveLength(2));
  expect(requests[1]).toEqual(requests[0]);
  second.close();
});
it("isolates a file selected for an existing waiting task from older same-name receiving files", async () => {
  const manifests: Manifest[] = [];
  mockAPI((_url, body) => {
    manifests.push(body.source_manifest!);
    return Response.json({ id: "waiting" });
  });
  const view = mount(
    <EditingPrepare
      task={
        {
          id: "waiting",
          device_id: "mac",
          manifest_frozen: false,
          source_manifest: null,
          source_directory: null,
        } as EditingTask
      }
      onChange={() => undefined}
    />,
  );
  await screen.findByRole("option", { name: /Studio/ });
  fireEvent.change(screen.getByLabelText("已授权目录"), {
    target: { value: "incoming" },
  });
  fireEvent.change(screen.getByLabelText("选择待提交文件"), {
    target: { files: [new File(["abc"], "重选.webm", { type: "video/webm" })] },
  });
  fireEvent.change(screen.getByLabelText("重选.webm 集号"), {
    target: { value: "2" },
  });
  fireEvent.click(
    screen.getByRole("button", { name: "提交这些文件，替换本次素材清单" }),
  );
  await waitFor(() => expect(manifests).toHaveLength(1));
  const source = manifests[0]!.files[0]!;
  expect(source).toMatchObject({
    name: "重选.webm",
    episode: 2,
    relative_path: `${source.media_id}.webm`,
  });
  view.close();
});
