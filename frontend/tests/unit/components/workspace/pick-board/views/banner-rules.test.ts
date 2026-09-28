/**
 * Which banners the pick data board shows above its tabs, and in what words
 * (P3-5; critique B18, B19, B20, C26). Pure: the board's resolved version,
 * the gateway's /sync answer and the request in, a list of banners out.
 */
import { describe, expect, it } from "@rstest/core";

import {
  bannersFor,
  STALE_AFTER_MS,
  type BannerBoard,
  type BannerInput,
} from "@/components/workspace/pick-board/views/banner-rules";
import type { PickMirrorStatus, PickSyncStatus } from "@/core/pick/sync-schema";
import type { GatewayResult } from "@/server/pick-board";

import { boardRules, request } from "../fixtures";

const AS_OF = "2026-09-24T03:40:00+00:00";
const NOW = new Date("2026-09-24T06:00:00Z");

function board(patch: Partial<BannerBoard> = {}): BannerBoard {
  return {
    scope: {
      schema: "pickm_v000007",
      asOf: AS_OF,
      versionId: 7,
      rules: boardRules(),
    },
    current: {
      id: 7,
      asOf: AS_OF,
      publishedAt: "2026-09-24T03:52:00+00:00",
      agentCatalogBatchId: "b-cat",
      agentKnowledgeBatchId: "b-kn",
    },
    freshness: null,
    postedImportedAt: null,
    warnings: [],
    requestedV: null,
    pinned: false,
    pruned: false,
    ignoredV: false,
    unreadable: false,
    ...patch,
  };
}

const MIRROR: PickMirrorStatus = {
  enabled: true,
  current: {
    id: 7,
    as_of: "2026-09-24T03:40:00.000Z",
    latest_snapshot: "2026-09-23",
    published_at: "2026-09-24T03:52:00.123456+00:00",
  },
  series_through: "2026-09-23",
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

function sync(
  patch: Partial<PickSyncStatus> = {},
): GatewayResult<PickSyncStatus> {
  return {
    ok: true,
    data: {
      configured: true,
      current: {
        id: "b-cat",
        shared: true,
        source_as_of: "2026-09-24T03:40:00.000Z",
        published_at: "2026-09-24T03:52:00.123456+00:00",
      },
      runs: [],
      mirror: MIRROR,
      ...patch,
    },
  };
}

function input(patch: Partial<BannerInput> = {}): BannerInput {
  return { board: board(), sync: sync(), req: request(), now: NOW, ...patch };
}

const keys = (i: BannerInput) => bannersFor(i).map((b) => b.key);
const texts = (i: BannerInput) => bannersFor(i).map((b) => b.text);
const only = (i: BannerInput) => {
  const list = bannersFor(i);
  expect(list).toHaveLength(1);
  return list[0]!;
};

describe("bannersFor", () => {
  it("warns about old catalog and posted imports even when the mirror is fresh and source markers are missing", () => {
    const stale = {
      ...board(),
      freshness: { importedAt: "2026-09-20T01:00:00Z" },
      postedImportedAt: "2026-09-20T02:00:00Z",
    };
    const banners = bannersFor(input({ board: stale }));
    expect(banners.map((b) => b.key)).toEqual(
      expect.arrayContaining(["catalog-stale", "posted-stale"]),
    );
    expect(banners.find((b) => b.key === "posted-stale")?.text).toContain(
      "不能据此认定未发布",
    );
    expect(banners.find((b) => b.key === "catalog-stale")?.text).toContain(
      "立即同步",
    );
  });

  it("judges an archived version's imports at capture time, not today's clock", () => {
    const historical = {
      ...board({ pinned: true, current: { ...board().current, id: 8 } }),
      freshness: { importedAt: "2026-09-24T01:00:00Z" },
      postedImportedAt: "2026-09-24T02:00:00Z",
    };
    const banners = bannersFor(
      input({ board: historical, now: new Date("2026-10-01T12:00:00Z") }),
    );
    expect(banners.map((b) => b.key)).not.toContain("catalog-stale");
    expect(banners.map((b) => b.key)).not.toContain("posted-stale");
  });
  it("shows nothing for the current version with a healthy mirror", () => {
    expect(bannersFor(input())).toEqual([]);
  });

  it("pinned: names both versions and links to the latest without v", () => {
    const b = only(
      input({
        board: board({
          pinned: true,
          requestedV: 5,
          scope: { ...board().scope, versionId: 5, asOf: AS_OF },
        }),
        req: request({ v: "5", tab: "all", q: "x" }),
      }),
    );
    expect(b.role).toBe("status");
    expect(b.text).toBe(
      "正在看智能体当时用的版本 v5（采集于 2026-09-24 03:40 UTC），当前最新 v7",
    );
    expect(b.link).toEqual({
      href: "/workspace/pick-data?tab=all&q=x",
      text: "切换到最新",
    });
  });

  it("pruned, unreadable and ignored v each say what happened to the link's v", () => {
    const pruned = only(
      input({ board: board({ pruned: true, requestedV: 3 }) }),
    );
    expect(pruned).toMatchObject({
      role: "status",
      text: "链接里的版本 v3 已清理，已显示当前版本 v7",
    });
    const unreadable = only(
      input({ board: board({ unreadable: true, requestedV: 4 }) }),
    );
    expect(unreadable.role).toBe("alert");
    expect(unreadable.text).toBe(
      "链接里的版本 v4 本页读不到（授权缺失），已显示当前版本 v7；请联系管理员",
    );
    const ignored = only(
      input({ board: board({ ignoredV: true, requestedV: 9 }) }),
    );
    expect(ignored.text).toBe(
      "链接里的版本 v9 不存在或未发布，已显示当前版本 v7",
    );
  });

  it("a personal import: says the agent reads the user's own catalog", () => {
    const current = {
      id: "b-mine",
      shared: false,
      source_as_of: "2026-09-20T01:00:00.000Z",
      published_at: "2026-09-20T01:00:00.000000+00:00",
    };
    const b = only(input({ sync: sync({ current }) }));
    expect(b.text).toBe("智能体当前用的是你手动导入的剧库，不在本页");
    expect(b.link).toEqual({
      href: "/workspace/pick-data?tab=imports",
      text: "看「同步与导入」",
    });
  });

  it("no current batch yet is not a personal import", () => {
    expect(bannersFor(input({ sync: sync({ current: null }) }))).toEqual([]);
  });

  it("a personal import still shows when the mirror status cannot be read", () => {
    const current = {
      id: "b-mine",
      shared: false,
      source_as_of: null,
      published_at: null,
    };
    const mirror = { error: "OperationalError" };
    expect(keys(input({ sync: sync({ current, mirror }) }))).toEqual([
      "personal",
      "mirror-unreadable",
    ]);
  });

  it("behind: quotes the shared batch's time, never the user's own (B20)", () => {
    const current = {
      id: "b-mine",
      shared: false,
      source_as_of: "2026-09-20T01:00:00.000Z",
      published_at: null,
    };
    const mirror = {
      ...MIRROR,
      behind: true,
      shared_source_as_of: "2026-09-24T15:40:00.000Z",
    };
    const list = texts(input({ sync: sync({ current, mirror }) }));
    expect(list).toContain(
      "资料页落后于智能体：镜像最新版本 v7 采集于 2026-09-24 03:40 UTC，智能体数据采集于 2026-09-24 15:40 UTC",
    );
    expect(list.join("\n")).not.toContain("2026-09-20");
  });

  it("behind without a shared time leaves the agent's time out", () => {
    const mirror = { ...MIRROR, behind: true, shared_source_as_of: null };
    expect(texts(input({ sync: sync({ mirror }) }))).toEqual([
      "资料页落后于智能体：镜像最新版本 v7 采集于 2026-09-24 03:40 UTC，智能体已换用更新的数据",
    ]);
  });

  it("alert: counts the failures and names the last one", () => {
    const mirror = {
      ...MIRROR,
      alert: true,
      consecutive_failures: 3,
      last_failure: "drift",
      last_failure_at: "2026-09-24T15:52:00.123456+00:00",
    };
    const b = only(input({ sync: sync({ mirror }) }));
    expect(b.role).toBe("alert");
    expect(b.text).toBe(
      "镜像同步已连续失败 3 次（最近：drift，2026-09-24 15:52 UTC）",
    );
  });

  it("switched off: says the board no longer follows RealShort (B19)", () => {
    const b = only(
      input({ sync: sync({ mirror: { ...MIRROR, enabled: false } }) }),
    );
    expect(b.key).toBe("disabled");
    expect(b.text).toBe(
      "镜像同步已关闭：本页停在 v7（采集于 2026-09-24 03:40 UTC），不再随 RealShort 更新",
    );
  });

  it("an as_of older than 14 hours warns on the reader alone (B19)", () => {
    const late = new Date(Date.parse(AS_OF) + STALE_AFTER_MS + 60_000);
    const b = only(input({ now: late }));
    expect(b.key).toBe("stale");
    expect(b.text).toBe(
      "镜像最新版本 v7 采集于 2026-09-24 03:40 UTC，已超过 14 小时没有新版本；同步可能停了",
    );
    expect(b.link?.href).toBe("/workspace/pick-data?tab=imports");
    const onTime = new Date(Date.parse(AS_OF) + STALE_AFTER_MS);
    expect(keys(input({ now: onTime }))).toEqual([]);
  });

  it("the stale line is 14 hours on the clock, not whatever the constant says", () => {
    const at = (h: number, m: number) =>
      new Date(Date.parse(AS_OF) + (h * 60 + m) * 60_000);
    expect(keys(input({ now: at(14, 1) }))).toEqual(["stale"]);
    expect(keys(input({ now: at(13, 59) }))).toEqual([]);
  });

  it("the 14-hour check reads the latest version, not a pinned older one", () => {
    // A candidate card pins v5, captured 15 hours ago; v7 is fresh.
    const v5 = {
      ...board().scope,
      versionId: 5,
      asOf: "2026-09-23T15:00:00+00:00",
    };
    expect(
      keys(input({ board: board({ pinned: true, requestedV: 5, scope: v5 }) })),
    ).toEqual(["pinned"]);
    // Both old: the stale line names the latest version and its time.
    const staleV7 = { ...board().current, asOf: "2026-09-23T15:30:00+00:00" };
    const list = bannersFor(
      input({
        board: board({
          pinned: true,
          requestedV: 5,
          scope: { ...v5, asOf: "2026-09-23T03:40:00+00:00" },
          current: staleV7,
        }),
      }),
    );
    expect(list.map((b) => b.key)).toEqual(["pinned", "stale"]);
    expect(list[1]?.text).toBe(
      "镜像最新版本 v7 采集于 2026-09-23 15:30 UTC，已超过 14 小时没有新版本；同步可能停了",
    );
  });

  it("the stale banner gives way to the switched-off and alert banners", () => {
    const late = new Date(Date.parse(AS_OF) + STALE_AFTER_MS + 60_000);
    const off = sync({ mirror: { ...MIRROR, enabled: false } });
    expect(keys(input({ now: late, sync: off }))).toEqual(["disabled"]);
    const failing = sync({
      mirror: { ...MIRROR, alert: true, consecutive_failures: 4 },
    });
    expect(keys(input({ now: late, sync: failing }))).toEqual(["alert"]);
    expect(
      keys(input({ now: late, sync: { ok: false, status: 502 } })),
    ).toEqual(["sync-unavailable", "stale"]);
  });

  it("no /sync: only one line says so, and no gateway banner shows (U31)", () => {
    for (const failed of [
      { ok: false, status: "unavailable" },
      { ok: false, status: 401 },
    ] as const) {
      expect(texts(input({ sync: failed }))).toEqual(["暂时拿不到同步状态"]);
    }
  });

  it("a failed mirror read says so; an absent or null mirror says nothing", () => {
    expect(
      texts(input({ sync: sync({ mirror: { error: "OperationalError" } }) })),
    ).toEqual(["暂时拿不到镜像同步状态"]);
    expect(bannersFor(input({ sync: sync({ mirror: undefined }) }))).toEqual(
      [],
    );
    expect(bannersFor(input({ sync: sync({ mirror: null }) }))).toEqual([]);
  });

  it("catalog_import_incomplete and source_stale_running come from the version (C26)", () => {
    const warnings = [
      {
        code: "catalog_import_incomplete",
        source: "pick_catalog",
        status: "failed",
        attemptedAt: "2026-09-24T01:00:00.000Z",
      },
      {
        code: "source_stale_running",
        source: "snapshot",
        status: "running",
        attemptedAt: "2026-09-24T02:10:00.000Z",
      },
      {
        code: "source_stale_running",
        source: "somewhere_new",
        status: "running",
        attemptedAt: null,
      },
    ];
    const list = bannersFor(input({ board: board({ warnings }) }));
    expect(list.map((b) => [b.role, b.text])).toEqual([
      ["alert", "本版本采集时剧单导入不完整，请在 RealShort 重跑剧单导入"],
      [
        "alert",
        "来源「每日快照（drama_observations）」自 2026-09-24 02:10 UTC 起一直未结束（可能已中断），本版本里它的数据停在更早一次成功的采集",
      ],
      [
        "alert",
        "来源「somewhere_new」自时间未知起一直未结束（可能已中断），本版本里它的数据停在更早一次成功的采集",
      ],
    ]);
  });

  it("rule drift names the theaters the page does not know", () => {
    const rules = boardRules((raw) => {
      raw.platformRules.newtv = {
        ...raw.platformRules.touchshort,
        key: "newtv",
        name: "NewTV",
      };
    });
    const b = only(
      input({ board: board({ scope: { ...board().scope, rules } }) }),
    );
    expect(b.text).toBe("RealShort 新增了剧场（newtv），部分标签可能过时");
  });

  it("keeps the table's order when several apply", () => {
    const mirror = {
      ...MIRROR,
      behind: true,
      alert: true,
      consecutive_failures: 3,
    };
    const current = {
      id: "b-mine",
      shared: false,
      source_as_of: null,
      published_at: null,
    };
    const warnings = [
      {
        code: "catalog_import_incomplete",
        source: "pick_catalog",
        status: "failed",
        attemptedAt: "2026-09-24T01:00:00.000Z",
      },
    ];
    expect(
      keys(
        input({
          board: board({ pinned: true, requestedV: 5, warnings }),
          sync: sync({ mirror, current }),
        }),
      ),
    ).toEqual([
      "pinned",
      "personal",
      "behind",
      "alert",
      "catalog_import_incomplete",
    ]);
  });
});
