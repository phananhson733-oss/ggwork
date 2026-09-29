/**
 * The imports tab's radar status (plan TR-25; design 3.7; D10): /sync's obs
 * key as the gateway computed it. Red banners are alerts and come first; the
 * other levels are status lines. Before the crons run (production today)
 * there is no banner and the panel says so in words, never with a zero. A
 * gateway from before TR-25 sends no obs key, and the panel is simply absent.
 */
import { afterEach, beforeEach, describe, expect, it, rs } from "@rstest/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";

import { DataImports } from "@/components/workspace/pick/data-imports";
import { forbiddenIn } from "@/core/pick/obs-format";
import { OBS_STATUS_TEXT } from "@/core/pick/obs-status";
import { syncStatusSchema } from "@/core/pick/sync-schema";

const api = rs.hoisted(() => ({ sync: null as unknown }));

rs.mock("@/core/auth/AuthProvider", () => ({
  useAuth: () => ({ user: { id: "u-1" } }),
}));

rs.mock("@/core/pick/api", () => ({
  getPickSyncStatus: async () => api.sync,
  startPickSync: async () => ({ status: "started" }),
  listPickBatches: async () => [],
  importPickData: async () => null,
}));

type Channel = Record<string, unknown>;

function channel(name: "trends" | "gsc", patch: Channel = {}): Channel {
  return {
    channel: name,
    live_set_id: null,
    live_published_at: null,
    latest_set_id: null,
    latest_published_at: null,
    latest_mode: null,
    last_run_at: null,
    banners: [],
    ...patch,
  };
}

/** What getPickSyncStatus hands the panel: the gateway's body, parsed like api.ts does. */
function sync(obs: unknown) {
  return syncStatusSchema.parse({
    configured: true,
    current: null,
    runs: [],
    mirror: null,
    ...(obs === undefined ? {} : { obs }),
  });
}

function obsOf(trends: Channel, gsc: Channel) {
  return {
    checked_at: "2026-09-25T04:10:00.000000+00:00",
    channels: [trends, gsc],
  };
}

const EMPTY = obsOf(channel("trends"), channel("gsc"));

const BUSY = obsOf(
  channel("trends", {
    live_set_id: "7a1c0e9b5d3f4a2e8b6c1d0f9e8a7b6c",
    live_published_at: "2026-09-24T01:52:10.000000+00:00",
    latest_set_id: "8b2d1f0a6e4c5b3f9c7d2e1a0f9b8c7d",
    latest_published_at: "2026-09-25T01:50:00.000000+00:00",
    latest_mode: "shadow",
    last_run_at: "2026-09-24T20:30:05.000000+00:00",
    banners: [
      { code: "stale_26h", level: "red" },
      { code: "shadow_mode", level: "info" },
    ],
  }),
  channel("gsc", {
    latest_set_id: "3f2e1d0c9b8a7f6e5d4c3b2a1f0e9d8c",
    latest_published_at: "2026-09-25T03:31:40.000000+00:00",
    latest_mode: "shadow",
    last_run_at: "2026-09-25T03:25:04.000000+00:00",
    banners: [
      { code: "gsc_unverifiable", level: "warn" },
      { code: "run_missed", level: "red" },
    ],
  }),
);

function renderImports() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <DataImports />
    </QueryClientProvider>,
  );
}

async function panel(): Promise<HTMLElement> {
  return screen.findByTestId("pick-obs-status");
}

beforeEach(() => {
  api.sync = sync(EMPTY);
});

afterEach(() => {
  cleanup();
});

describe("the imports tab's radar status", () => {
  it("shows red banners as alerts, first, naming the channel", async () => {
    api.sync = sync(BUSY);
    renderImports();
    const found = await panel();
    const banners = [...found.querySelectorAll("[data-obs-banner]")];
    expect(banners.map((b) => b.getAttribute("data-obs-banner"))).toEqual([
      "red",
      "red",
      "warn",
      "info",
    ]);
    const alerts = screen.getAllByRole("alert");
    expect(alerts).toHaveLength(2);
    expect(alerts[0]?.textContent).toContain("Google Trends");
    expect(alerts[0]?.textContent).toContain(OBS_STATUS_TEXT.stale_26h);
    expect(alerts[1]?.textContent).toContain("GSC");
    expect(alerts[1]?.textContent).toContain(OBS_STATUS_TEXT.run_missed);
    expect(found.textContent).toContain(OBS_STATUS_TEXT.gsc_unverifiable);
    expect(found.textContent).toContain(OBS_STATUS_TEXT.shadow_mode);
  });

  it("says where each channel stands: the live set, the latest set, the last run", async () => {
    api.sync = sync(BUSY);
    renderImports();
    const text = (await panel()).textContent ?? "";
    expect(text).toContain("当前生效集合 2026-09-24 01:52 UTC 发布");
    expect(text).toContain("最新集合是影子，2026-09-25 01:50 UTC 发布");
    expect(text).toContain("最近一次运行 2026-09-24 20:30 UTC 开始");
    expect(text).toContain("没有生效的 live 集合");
    expect(text).toContain("状态按 2026-09-25 04:10 UTC 计算");
  });

  it("says in words that nothing has run yet, with no banner", async () => {
    renderImports();
    const text = (await panel()).textContent ?? "";
    expect(text).toContain(
      "趋势雷达还没有运行记录，也没有发布过观测集合：采集尚未开启，观测数据未就绪",
    );
    expect(screen.queryAllByRole("alert")).toHaveLength(0);
    expect(forbiddenIn(text)).toEqual([]);
  });

  it("says so when the gateway could not read the status", async () => {
    api.sync = sync({ error: "OperationalError" });
    renderImports();
    expect((await panel()).textContent).toContain(
      "暂时读不到趋势雷达状态（OperationalError）",
    );
  });

  it("is absent for a gateway from before TR-25, or a malformed key", async () => {
    for (const obs of [undefined, null, { channels: "x" }]) {
      api.sync = sync(obs);
      renderImports();
      await screen.findByTestId("pick-sync-current");
      expect(screen.queryByTestId("pick-obs-status")).toBeNull();
      cleanup();
    }
  });

  it("words a code this page does not know yet at the gateway's level", async () => {
    api.sync = sync(
      obsOf(
        channel("trends", { banners: [{ code: "future_code", level: "red" }] }),
        channel("gsc"),
      ),
    );
    renderImports();
    await panel();
    expect(screen.getByRole("alert").textContent).toContain(
      "状态码 future_code：这个页面版本还没有它的说明",
    );
  });

  it("keeps every line free of zero wording", async () => {
    api.sync = sync(BUSY);
    renderImports();
    expect(forbiddenIn((await panel()).textContent ?? "")).toEqual([]);
  });
});
