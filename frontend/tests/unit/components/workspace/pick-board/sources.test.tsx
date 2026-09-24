// PORTED_FROM: realshort@816ca2e tests/pick-sources.test.ts（:95-106，Sources 组件那一条）
// 本地改动：node:test 换成 @rstest/core；now 换成版本的 asOf（本页的「现在」是镜像采集时点，不是墙上时钟），
// 另加一条：僵死判定按 asOf 算。来源清单与 CHECK 迁移的比对属于 RealShort 仓库，不搬。
import assert from "node:assert/strict";

import { test } from "@rstest/core";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

import { Sources } from "@/components/workspace/pick-board/sources";
import {
  OBSERVE_SOURCES,
  SOURCE_LABELS,
  type ObserveSource,
  type SourceState,
} from "@/core/pick-board/source-types";
import type { PickFreshness } from "@/server/pick-board";

const AS_OF = new Date("2026-09-23T12:00:00Z");
const ago = (ms: number) => new Date(AS_OF.getTime() - ms).toISOString();

const state = (
  source: ObserveSource,
  over: Partial<SourceState> = {},
): SourceState => ({
  source,
  status: "success",
  attemptedAt: ago(3_600_000),
  completedAt: ago(3_000_000),
  details: {},
  ...over,
});

const fresh: PickFreshness = {
  importedAt: new Date("2026-09-23T02:45:00Z"),
  rows: 40_000,
  withSignal: 2_000,
  signals: 2_500,
  posted: 80,
  rsCanonical: 30_000,
  rsCandidates: 500,
  rsSyncedAt: new Date("2026-09-23T06:00:00Z"),
};

const REELSHORT: Partial<Record<ObserveSource, SourceState>> = {
  catalog: state("catalog"),
  snapshot: state("snapshot"),
  bill: state("bill"),
  gsc: state("gsc"),
};

/** 页底来源状态里每一行（<li>…</li>）的可见文字，按标签取 */
function rowsOf(
  sources: Partial<Record<ObserveSource, SourceState>>,
): Map<string, string> {
  const html = renderToStaticMarkup(
    createElement(Sources, { fresh, sources, asOf: AS_OF }),
  );
  const out = new Map<string, string>();
  for (const li of html.match(/<li\b[^>]*>[\s\S]*?<\/li>/g) ?? []) {
    const text = li
      .replace(/<[^>]+>/g, "|")
      .replace(/\|+/g, "|")
      .replace(/^\||\|$/g, "");
    const label = Object.values(SOURCE_LABELS).find((l) => text.startsWith(l));
    if (label) out.set(label, text);
  }
  return out;
}

test("Sources：五个来源各一行；sources 里没有 pick_catalog 时这一行照常渲染、时间列是「—」，不报错", () => {
  const rows = rowsOf(REELSHORT);
  assert.deepEqual(
    [...rows.keys()],
    OBSERVE_SOURCES.map((s) => SOURCE_LABELS[s]),
  );
  assert.equal(
    rows.get("剧单导入（pick_catalog）"),
    "剧单导入（pick_catalog）|尚无带标记的导入记录|—",
  );
  /* ReelShort 的四行照旧走 sourceStatus */
  assert.equal(
    rows.get(SOURCE_LABELS.snapshot),
    `${SOURCE_LABELS.snapshot}|最近采集完成|${ago(3_000_000).slice(0, 16).replace("T", " ")} UTC`,
  );
  /* sources 整个为空（版本 meta.sources 里没有）也不报错 */
  assert.equal(rowsOf({}).size, OBSERVE_SOURCES.length);
});

test("Sources：过期与僵死都按版本的 asOf 判，不看墙上时钟", () => {
  const running = state("pick_catalog", {
    status: "running",
    attemptedAt: ago(10 * 60_000),
    completedAt: null,
  });
  const stale = state("pick_catalog", {
    status: "running",
    attemptedAt: ago(60 * 60_000),
    completedAt: null,
  });
  assert.match(
    rowsOf({ pick_catalog: running }).get(SOURCE_LABELS.pick_catalog) ?? "",
    /正在导入/,
  );
  assert.match(
    rowsOf({ pick_catalog: stale }).get(SOURCE_LABELS.pick_catalog) ?? "",
    /失败或中断/,
  );
  assert.match(rowsOf(REELSHORT).get(SOURCE_LABELS.bill) ?? "", /最近采集完成/);
});

test("Sources：页底不显示金额，取货链接是 https", () => {
  const html = renderToStaticMarkup(
    createElement(Sources, { fresh, sources: REELSHORT, asOf: AS_OF }),
  );
  assert.doesNotMatch(html, /\$|USD/);
  for (const m of html.matchAll(/href="([^"]*)"/g))
    assert.match(m[1] ?? "", /^https:\/\//);
});
