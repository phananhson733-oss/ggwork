import { afterEach, expect, it, rs } from "@rstest/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

import { EditingDevices } from "@/components/workspace/editing/editing-devices";
import { AuthProvider, type User } from "@/core/auth/AuthProvider";

rs.mock("next/navigation", () => ({
  useRouter: () => ({ push: rs.fn() }),
  usePathname: () => "/workspace/editing/new",
}));
afterEach(() => {
  cleanup();
  rs.restoreAllMocks();
});

it("rechecks devices without submitting the surrounding editing draft", async () => {
  rs.spyOn(globalThis, "fetch").mockResolvedValue(
    Response.json({}, { status: 503 }),
  );
  const submit = rs.fn((event: React.FormEvent) => event.preventDefault());
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, retryDelay: 0 } },
  });
  render(
    <AuthProvider initialUser={{ id: "owner" } as User}>
      <QueryClientProvider client={client}>
        <form onSubmit={submit}>
          <EditingDevices value="" onChange={() => undefined} />
        </form>
      </QueryClientProvider>
    </AuthProvider>,
  );
  fireEvent.click(await screen.findByRole("button", { name: "重新检查设备" }));
  expect(submit).not.toHaveBeenCalled();
  client.clear();
});

it("explains native setup blockers and never renders unknown worker text", async () => {
  rs.spyOn(globalThis, "fetch").mockResolvedValue(
    Response.json({
      items: [
        {
          id: "mac",
          name: "Studio",
          ready: false,
          online: true,
          grants: [],
          reasons: [
            "source_audio_video_required",
            "secret /Users/private/config token=abc",
          ],
        },
      ],
    }),
  );
  const client = new QueryClient();
  render(
    <AuthProvider initialUser={{ id: "owner" } as User}>
      <QueryClientProvider client={client}>
        <EditingDevices value="mac" onChange={() => undefined} />
      </QueryClientProvider>
    </AuthProvider>,
  );
  await screen.findByText("素材需要同时包含视频和音轨，请在 Mac 检查所选文件");
  expect(screen.queryByText(/secret|source_audio_video_required/)).toBeNull();
  expect(
    screen.getByText("状态暂不可识别，请重新检查；如仍未恢复，请联系管理员"),
  ).toBeTruthy();
  client.clear();
});

it("clears the one-time pairing credential when the account changes", async () => {
  const { useAuth } = await import("@/core/auth/AuthProvider");
  function AccountSwitch() {
    const { applyUser } = useAuth();
    return (
      <button onClick={() => applyUser({ id: "other-owner" } as User)}>
        Switch account
      </button>
    );
  }
  rs.spyOn(globalThis, "fetch").mockImplementation(async (_input, init) =>
    Response.json(
      init?.method === "POST"
        ? { device: { id: "mac" }, token: "pairing-secret" }
        : { items: [] },
    ),
  );
  const client = new QueryClient();
  render(
    <AuthProvider initialUser={{ id: "owner" } as User}>
      <QueryClientProvider client={client}>
        <AccountSwitch />
        <EditingDevices value="" onChange={() => undefined} />
      </QueryClientProvider>
    </AuthProvider>,
  );
  fireEvent.click(screen.getByRole("button", { name: "生成连接凭证" }));
  await screen.findByLabelText("一次性设备连接凭证");
  fireEvent.click(screen.getByRole("button", { name: "Switch account" }));
  expect(screen.queryByLabelText("一次性设备连接凭证")).toBeNull();
  client.clear();
});
