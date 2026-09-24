/**
 * The imports tab's sync panel with /sync's mirror key (P3-5; ★U20): the
 * mirror version and curve end, failures, a stuck lock, a failed mirror read,
 * and nothing at all when there is no mirror (SQLite, or a gateway from
 * before P2-8b). The imports panel itself no longer carries a page title:
 * the pick data page supplies it.
 */
import { afterEach, beforeEach, describe, expect, it, rs } from "@rstest/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import type { ReactNode } from "react";

import { DataImports } from "@/components/workspace/pick/data-imports";
import { PickWelcome } from "@/components/workspace/pick/pick-welcome";
import { SyncStatus } from "@/components/workspace/pick/sync-status";
import type { PickSyncStatus } from "@/core/pick/sync-schema";

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

rs.mock("@/components/ai-elements/prompt-input", () => ({
  usePromptInputController: () => ({
    textInput: { setInput: () => undefined },
  }),
}));

rs.mock("next/link", () => ({
  default: ({ href, children }: { href: string; children: ReactNode }) => (
    <a href={href}>{children}</a>
  ),
}));

const MIRROR = {
  enabled: true,
  current: {
    id: 7,
    as_of: "2026-09-24T03:40:00.000Z",
    latest_snapshot: "2026-09-23",
    published_at: "2026-09-24T03:52:00.123456+00:00",
  },
  series_through: "2026-09-22",
  trimmed_before: "2026-06-25",
  behind: false,
  consecutive_failures: 0,
  last_failure_at: null,
  last_failure: null,
  alert: false,
  warnings: [],
  lock_stuck: null,
  shared_source_as_of: "2026-09-24T03:40:00.000Z",
};

function sync(mirror: unknown): PickSyncStatus {
  return {
    configured: true,
    current: {
      id: "b-cat",
      shared: true,
      source_as_of: "2026-09-24T03:40:00.000Z",
      published_at: null,
      rows: 12,
    },
    runs: [],
    mirror,
  } as PickSyncStatus;
}

function renderWithClient(node: ReactNode) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>{node}</QueryClientProvider>,
  );
}

async function mirrorLine(): Promise<string> {
  await screen.findByTestId("pick-sync-current");
  return screen.queryByTestId("pick-sync-mirror")?.textContent ?? "";
}

beforeEach(() => {
  api.sync = sync(MIRROR);
});

afterEach(() => {
  cleanup();
});

describe("SyncStatus with the mirror key", () => {
  it("shows the mirror version, its capture time and the curve end", async () => {
    renderWithClient(<SyncStatus />);
    const line = await mirrorLine();
    expect(line).toContain("镜像 v7 采集于 2026-09-24 03:40 UTC");
    expect(line).toContain("曲线截至 2026-09-22");
    expect(line).not.toContain("连续失败");
  });

  it("names the failures, a switched-off mirror and a stuck lock", async () => {
    api.sync = sync({
      ...MIRROR,
      enabled: false,
      consecutive_failures: 2,
      last_failure: "degraded:RowTooLargeError",
      last_failure_at: "2026-09-24T15:52:00.123456+00:00",
      lock_stuck: {
        pid: 4242,
        holder: "backfill",
        since: "2026-09-24T02:00:00.000000+00:00",
      },
    });
    renderWithClient(<SyncStatus />);
    const line = await mirrorLine();
    expect(line).toContain("镜像同步已关闭");
    expect(line).toContain(
      "镜像连续失败 2 次（最近：degraded:RowTooLargeError，2026-09-24 15:52 UTC）",
    );
    expect(line).toContain(
      "镜像同步锁被 backfill 占着，自 2026-09-24 02:00 UTC 起超过 60 分钟没释放（进程 4242）",
    );
  });

  it("a stuck lock with no pid, holder or time still shows", async () => {
    api.sync = sync({
      ...MIRROR,
      lock_stuck: { pid: null, holder: null, since: null },
    });
    renderWithClient(<SyncStatus />);
    expect(await mirrorLine()).toContain(
      "镜像同步锁被 未知进程 占着，自 时间未知 起超过 60 分钟没释放（进程 未知）",
    );
  });

  it("says so when the gateway could not read the mirror", async () => {
    api.sync = sync({ error: "OperationalError" });
    renderWithClient(<SyncStatus />);
    expect(await mirrorLine()).toBe("暂时读不到镜像状态（OperationalError）");
  });

  it("says nothing about a mirror that is not there", async () => {
    for (const mirror of [null, undefined]) {
      api.sync = sync(mirror);
      renderWithClient(<SyncStatus />);
      expect(await mirrorLine()).toBe("");
      cleanup();
    }
  });

  it("a mirror with no version yet", async () => {
    api.sync = sync({ ...MIRROR, current: null, series_through: null });
    renderWithClient(<SyncStatus />);
    expect(await mirrorLine()).toContain("镜像还没有发布版本");
  });

  it("explains that the agent and the board share one capture (U20)", async () => {
    renderWithClient(<SyncStatus />);
    await mirrorLine();
    expect(
      screen.getByText(/智能体的候选池与本页的镜像版本同一次采集/),
    ).toBeTruthy();
    expect(screen.getByText(/max\(20, 1%\)/)).toBeTruthy();
  });
});

describe("the imports panel inside the pick data page", () => {
  it("has no page title of its own; the page gives it", async () => {
    renderWithClient(<DataImports />);
    await screen.findByTestId("pick-sync-current");
    expect(screen.queryByRole("heading", { level: 1 })).toBeNull();
    expect(screen.getByText(/剧库用于筛选，知识资料用于解释规则/)).toBeTruthy();
  });

  it("the welcome screen sends first-time users to the imports tab", () => {
    render(<PickWelcome />);
    expect(
      screen.getByText("第一次使用？先导入剧库与知识资料").getAttribute("href"),
    ).toBe("/workspace/pick-data?tab=imports");
  });
});
