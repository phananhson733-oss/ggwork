// PORTED_FROM: realshort@816ca2e tests/pick-sources.test.ts
// 本地改动：:108-143 的 45 分钟僵死判定改在「从版本 meta.sources 读出来的状态」上跑，now 取版本的 as_of（B15）；
// 纯函数那几条已在 tests/unit/core/pick-board/source-types.test.ts，Sources 组件那条（:95-106）归 P3-4。
// 另加 meta.sources 的归一：版本数据按 P2 的宽口径写入，不认识的来源、坏形状的项在读出时丢掉。
import { describe, expect, it } from "@rstest/core";

import {
  STALE_RUNNING_MS,
  catalogImportStatus,
  sourceRowStatus,
  sourceStatus,
} from "@/core/pick-board/source-types";
import { sourcesOf } from "@/server/pick-board/source-state";

const AS_OF = new Date("2026-09-23T12:00:00Z");
const before = (ms: number) => new Date(AS_OF.getTime() - ms).toISOString();

/** meta.sources as a version stores it: RealShort's observe_sources rows, keyed by source. */
function metaSources(over: Record<string, unknown> = {}) {
  return {
    bill: {
      source: "bill",
      status: "success",
      attemptedAt: before(3_600_000),
      completedAt: before(3_000_000),
      details: { rows: 12, ratio: 50 },
    },
    ...over,
  };
}

function pickCatalog(status: string, attemptedAt: string) {
  return {
    pick_catalog: {
      source: "pick_catalog",
      status,
      attemptedAt,
      completedAt: null,
      details: {},
    },
  };
}

describe("sourcesOf: meta.sources as the page reads it", () => {
  it("keeps each known source as it was exported", () => {
    const sources = sourcesOf(metaSources());
    expect(sources.bill).toEqual({
      source: "bill",
      status: "success",
      attemptedAt: before(3_600_000),
      completedAt: before(3_000_000),
      details: { rows: 12, ratio: 50 },
    });
  });

  it("drops what it cannot show: unknown sources, unknown states, non-objects", () => {
    const sources = sourcesOf(
      metaSources({
        pay: { source: "pay", status: "success", attemptedAt: "", details: {} },
        gsc: { source: "gsc", status: "paused", attemptedAt: before(1) },
        snapshot: "success",
      }),
    );
    expect(Object.keys(sources)).toEqual(["bill"]);
  });

  it("an absent or malformed meta.sources is no sources, not an error", () => {
    for (const raw of [null, undefined, [], "bill", 3])
      expect(sourcesOf(raw)).toEqual({});
  });

  it("loose scalars from the version are made safe to show", () => {
    const { bill } = sourcesOf({
      bill: {
        status: "failed",
        attemptedAt: null,
        completedAt: 17,
        details: "x",
      },
    });
    expect(bill).toEqual({
      source: "bill",
      status: "failed",
      attemptedAt: "",
      completedAt: null,
      details: {},
    });
    expect(sourceStatus(bill, AS_OF)).toBe("采集失败，尚无完整批次");
  });
});

describe("45 分钟僵死判定：按版本的 as_of 判，不按墙上时钟（rs:tests/pick-sources.test.ts:108-143）", () => {
  it("剧单导入 running 满 45 分钟：判中断，提示整次重跑", () => {
    const { pick_catalog } = sourcesOf(
      pickCatalog("running", before(STALE_RUNNING_MS)),
    );
    const text = catalogImportStatus(pick_catalog, AS_OF);
    expect(text).toMatch(/剧单导入失败或中断/);
    expect(text).toMatch(/只写了一半/);
    expect(text).toMatch(/重跑 pnpm catalog-import/);
  });

  it("45 分钟之内：写正在导入，不说失败", () => {
    const { pick_catalog } = sourcesOf(
      pickCatalog("running", before(STALE_RUNNING_MS - 60_000)),
    );
    expect(catalogImportStatus(pick_catalog, AS_OF)).toMatch(
      /^正在导入（开始于 .* UTC）/,
    );
  });

  it("同一条 running，换一个更晚的 as_of 才判中断：时点只取传入的那个", () => {
    const { pick_catalog } = sourcesOf(
      pickCatalog("running", before(STALE_RUNNING_MS - 60_000)),
    );
    const later = new Date(AS_OF.getTime() + 60_000);
    expect(catalogImportStatus(pick_catalog, later)).toMatch(/失败或中断/);
  });

  it("失败的剧单导入写开始时间；开始时间读不出来的 running 按正在导入算", () => {
    const failed = sourcesOf(pickCatalog("failed", "2026-09-23T02:45:00.000Z"));
    expect(catalogImportStatus(failed.pick_catalog, AS_OF)).toMatch(
      /开始于 2026-09-23 02:45 UTC/,
    );
    const garbled = sourcesOf(pickCatalog("running", "garbage"));
    expect(catalogImportStatus(garbled.pick_catalog, AS_OF)).toMatch(
      /^正在导入/,
    );
  });

  it("版本里没有 pick_catalog：那一行照常给文案，其它来源走 sourceStatus", () => {
    const sources = sourcesOf(metaSources());
    expect(sourceRowStatus("pick_catalog", sources.pick_catalog, AS_OF)).toBe(
      "尚无带标记的导入记录",
    );
    expect(sourceRowStatus("bill", sources.bill, AS_OF)).toBe("最近采集完成");
  });
});
