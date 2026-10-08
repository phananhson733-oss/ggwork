import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { Pool } from "pg";

import {
  matchRow,
  postedCompatible,
  postedTitleKey,
  signalEvidenceOn,
  summarizePosts,
  toAccountRows,
  toCatalogRow,
  toPostedRows,
  toSignalRows,
  writeCatalogMarked,
  type CatalogWriteSteps,
  type MatchTables,
  type RawAccount,
  type RawCatalogRow,
  type RawPostedDrama,
} from "../src/lib/pick/catalog-import";

/*
 * 样本按 build.py 2026-09-10 的真实输出形状写（各 kind 的字段集合）：
 *   kd  {s,r,d,best,days,first,note,h}   kw {s,w,weeks,d,h}   sm/mg {s,g}
 *   fh/ghh {s,d}   gh/gn {s,d,t}   dbn {s,t}   smd/sh {s}
 */
const kalos: RawCatalogRow = {
  k: "kalos-the-dragon-s-contract-x",
  p: "kalos",
  src: "英语剧单",
  t: "The Dragon's Contract",
  cn: "龙之契约（A）",
  lang: "英语",
  kind: "本土AI真人-番茄引入",
  date: "2026-09-07",
  pan: "https://pan.baidu.com/s/1blAEj1pKDIpoUBKujN5UgA?pwd=v93q",
  pw: "v93q",
  ep: 56,
  pay: 11,
  yt: false,
  n: 2,
  sig: [
    { s: "kd", r: 1, d: "2026-09-09", best: 1, days: 3, first: "2026-09-07", note: "爆单主推", h: [["2026-09-09", 1]] },
    { s: "kd", r: 6, d: "2026-08-02", best: 6, days: 1, first: "2026-08-02", note: "", h: [["2026-08-02", 6]] },
    { s: "kw", w: "8.10–8.16", weeks: 1, d: "2026-08-10", h: [["2026-08-10", "8.10–8.16"]] },
    { s: "sm", g: "S" },
  ],
};

const empty: MatchTables = {
  siteLocales: new Map(),
  canonical: new Map(),
  legacyLocales: new Map(),
};

test("鹊娱榜信号与 KalosTV 日榜同形：evidenceOn 取榜单日期，rank 取名次，历史与鹊娱 id 留在 payload", () => {
  const [qc, qr] = toSignalRows({
    ...kalos,
    sig: [
      { s: "qc", r: 3, d: "2026-09-11", best: 3, days: 1, first: "2026-09-11", h: [["2026-09-11", 3]], qy: 11131, pid: "782194" },
      { s: "qr", r: 25, d: "2026-09-11", best: 25, days: 1, first: "2026-09-11", h: [["2026-09-11", 25]] },
    ],
  });
  assert.equal(qc!.kind, "qc");
  assert.equal(qc!.evidenceOn, "2026-09-11");
  assert.equal(qc!.rank, 3);
  assert.deepEqual((qc!.payload as { h: unknown }).h, [["2026-09-11", 3]]);
  assert.equal((qc!.payload as { qy: unknown }).qy, 11131);
  assert.equal(qr!.evidenceOn, "2026-09-11");
  assert.equal(qr!.rank, 25);
});

test("同 kind 多条信号靠 ord 区分，第二条不丢", () => {
  const sig = toSignalRows(kalos);
  assert.equal(sig.length, 4);
  assert.deepEqual(
    sig.map((s) => [s.kind, s.ord]),
    [["kd", 0], ["kd", 1], ["kw", 2], ["sm", 3]],
  );
  // 主键 (row_key, kind, ord) 在这四条里唯一
  assert.equal(new Set(sig.map((s) => `${s.rowKey}|${s.kind}|${s.ord}`)).size, 4);
});

test("证据日期：榜单 / 周起 / 入榜日期有，评级与备注没有", () => {
  assert.equal(signalEvidenceOn({ s: "kd", d: "2026-09-09" }), "2026-09-09");
  assert.equal(signalEvidenceOn({ s: "kw", d: "2026-08-10" }), "2026-08-10");
  assert.equal(signalEvidenceOn({ s: "gn", d: "2026-09-09", t: "新剧推荐" }), "2026-09-09");
  assert.equal(signalEvidenceOn({ s: "sm", g: "SSS" }), null, "评级没有自己的日期");
  assert.equal(signalEvidenceOn({ s: "dbn", t: "重点剧集" }), null, "备注没有自己的日期");
  assert.equal(signalEvidenceOn({ s: "kd", d: "9.9" }), null, "不是 YYYY-MM-DD 的不当日期");
});

test("信号行：名次 / 评级 / 一句话各归各列，其余进 payload", () => {
  const [kd, , kw, sm] = toSignalRows(kalos);
  assert.equal(kd.rank, 1);
  assert.equal(kd.note, "爆单主推");
  assert.equal(kd.grade, "");
  assert.deepEqual(Object.keys(kd.payload).sort(), ["best", "d", "days", "first", "h"]);
  assert.equal(kw.rank, null);
  assert.equal(kw.payload.w, "8.10–8.16");
  assert.equal(sm.grade, "S");
  assert.equal(sm.evidenceOn, null);
  // GoodShort 的理由原文走 t
  const [gn] = toSignalRows({ ...kalos, sig: [{ s: "gn", d: "2026-09-09", t: "站内高充值" }] });
  assert.equal(gn.note, "站内高充值");
});

test("剧库行：最近证据日期取所有信号里最大的那个，没有日期的信号不代填", () => {
  const row = toCatalogRow(kalos, { inSiteIds: [], legacyOnly: false, siteOther: false });
  assert.equal(row.latestEvidenceOn, "2026-09-09");
  assert.equal(row.hasSignal, true);
  assert.equal(row.mergedRows, 2);
  assert.equal(row.youtube, false);
  assert.equal(row.titleKey, "the-dragon-s-contract");
  const onlyGrade = toCatalogRow({ ...kalos, sig: [{ s: "sm", g: "A" }] }, { inSiteIds: [], legacyOnly: false, siteOther: false });
  assert.equal(onlyGrade.latestEvidenceOn, null, "只有评级时日期未知，不拿剧单日期代填");
  assert.equal(onlyGrade.hasSignal, true);
  const none = toCatalogRow({ ...kalos, sig: [] }, { inSiteIds: [], legacyOnly: false, siteOther: false });
  assert.equal(none.hasSignal, false);
});

test("下架与重新分销互斥：有 reoff 的行 offOn 为空", () => {
  const off = toCatalogRow({ ...kalos, off: "2026-02-19" }, { inSiteIds: [], legacyOnly: false, siteOther: false });
  assert.equal(off.offOn, "2026-02-19");
  assert.equal(off.reoffNote, "");
  const re = toCatalogRow(
    { ...kalos, off: "2026-02-19", reoff: "曾下架 2026-02-19，2026-05-28 重新分销" },
    { inSiteIds: [], legacyOnly: false, siteOther: false },
  );
  assert.equal(re.offOn, null, "重新分销的行不再算下架");
  assert.match(re.reoffNote, /重新分销/);
});

test("匹配：同语种在售 / 只有他语种在售 / 只对上旧站 / 都没对上", () => {
  const k = "the-dragon-s-contract";
  const inSite: MatchTables = {
    siteLocales: new Map([[k, ["en", "es"]]]),
    canonical: new Map([[k, [{ id: "b1", locale: "en" }, { id: "b2", locale: "es" }]]]),
    legacyLocales: new Map([[k, ["en"]]]),
  };
  assert.deepEqual(matchRow(kalos, inSite), { inSiteIds: ["b1", "b2"], legacyOnly: false, siteOther: false });
  // 这一行是日语，站上只有英西两种 → 同名·他语种在售，且不挂别的语种的 id
  assert.deepEqual(matchRow({ ...kalos, lang: "日语" }, inSite), { inSiteIds: [], legacyOnly: false, siteOther: true });
  const legacyOnly: MatchTables = { ...empty, legacyLocales: new Map([[k, ["en"]]]) };
  assert.deepEqual(matchRow(kalos, legacyOnly), { inSiteIds: [], legacyOnly: true, siteOther: false });
  assert.deepEqual(matchRow(kalos, empty), { inSiteIds: [], legacyOnly: false, siteOther: false });
});

test("匹配：中文名也参与；英文名对上本站时中文名对上旧站不算旧站收录", () => {
  const cnKey = "龙之契约-a";
  const tables: MatchTables = {
    siteLocales: new Map([["the-dragon-s-contract", ["en"]]]),
    canonical: new Map(),
    legacyLocales: new Map([[cnKey, ["zh"]]]),
  };
  const m = matchRow(kalos, tables);
  assert.equal(m.legacyOnly, false, "在售的行不算旧站收录");
  const cnOnly = matchRow({ ...kalos, t: "zzzz-nomatch" }, tables);
  assert.equal(cnOnly.legacyOnly, true, "只有中文名对上旧站");
});

test("匹配：短于 4 个字符的键不参与（全是误配）", () => {
  const tables: MatchTables = { ...empty, siteLocales: new Map([["ab", ["en"]]]) };
  assert.deepEqual(matchRow({ ...kalos, t: "AB", cn: undefined }, tables), { inSiteIds: [], legacyOnly: false, siteOther: false });
});

test("运营表剧名归一与 posted.py 的 title_key 等价", () => {
  assert.equal(postedTitleKey("Broken Contract and Four Cubs"), "broken contract and four cubs");
  assert.equal(postedTitleKey("The Dragon’s Contract"), "the dragons contract", "撇号去掉不留空格");
  assert.equal(postedTitleKey("Ｆｕｌｌ　Ｗｉｄｔｈ"), "full width", "NFKC 折叠全角");
  assert.equal(postedTitleKey("  a_b--c  "), "a b c");
  assert.equal(postedTitleKey(null), "");
});

test("发布记录只挂到同剧场同语种的行；「其他」至少不是 ReelShort", () => {
  const dramabox: RawCatalogRow = { ...kalos, k: "dramabox-the-dragon-s-contract-x", p: "dramabox", lang: "英语", sig: [] };
  const rows = [kalos, dramabox, { ...kalos, k: "kalos-the-dragon-s-contract-x-2", lang: "日语", sig: [] }];
  const d = { sd: "SD-000001", t: "The Dragon's Contract", plat: "DramaBox", lang: "英语", posts: [] };
  assert.equal(postedCompatible(d, kalos), false);
  assert.equal(postedCompatible(d, dramabox), true);
  assert.equal(postedCompatible({ ...d, plat: "其他" }, { ...kalos, p: "reelshort" }), false);
  assert.equal(postedCompatible({ ...d, plat: null, lang: null }, kalos), true, "没填就不约束");
  const [p] = toPostedRows([d], rows);
  assert.deepEqual(p.rowKeys, ["dramabox-the-dragon-s-contract-x"]);
  const [loose] = toPostedRows([{ ...d, plat: null, lang: null }], rows);
  assert.deepEqual(loose.rowKeys.sort(), ["dramabox-the-dragon-s-contract-x", "kalos-the-dragon-s-contract-x", "kalos-the-dragon-s-contract-x-2"]);
});

test("发布记录对 ReelShort 剧：只比剧名，语种按中文名换成 locale 比，剧场填了别家就不挂", () => {
  const canonical = [
    { id: "r-en", title: "The Dragon's Contract", locale: "en" },
    { id: "r-ja", title: "The Dragon's Contract", locale: "ja" },
    { id: "r-x", title: "Other Show", locale: "en" },
  ];
  const [en] = toPostedRows([{ sd: "SD-1", t: "The Dragon’s Contract", plat: "ReelShort", lang: "英语", posts: [] }], [], canonical);
  assert.deepEqual(en.dramaIds, ["r-en"], "撇号写法不同也对得上；只挂英语那一行");
  const [any] = toPostedRows([{ sd: "SD-2", t: "The Dragon's Contract", plat: null, lang: null, posts: [] }], [], canonical);
  assert.deepEqual(any.dramaIds.sort(), ["r-en", "r-ja"], "没填就两个语种都挂");
  const [db] = toPostedRows([{ sd: "SD-3", t: "The Dragon's Contract", plat: "DramaBox", lang: null, posts: [] }], [], canonical);
  assert.deepEqual(db.dramaIds, [], "填了 DramaBox 的记录不挂 ReelShort 剧");
  const [other] = toPostedRows([{ sd: "SD-4", t: "The Dragon's Contract", plat: "其他", lang: null, posts: [] }], [], canonical);
  assert.deepEqual(other.dramaIds, [], "「其他」至少不是 ReelShort");
});

test("发布记录：帖子聚合与 posted.py 同口径——只数已回填 / 已公开，待公开另计，指标空着不当 0 加", () => {
  const [p] = toPostedRows(
    [
      {
        sd: "SD-000002",
        t: "X",
        src: ["Reelshort榜单"],
        cats: ["爱情", "复仇"],
        who: ["高璇"],
        created: "2026-09-07",
        updated: "2026-09-08",
        posts: [
          { d: "2026-08-24", st: "已回填", acct: "b-acct", views: 193, md: "2026-08-28" },
          { d: "2026-08-30", st: "已公开", acct: "a-acct", views: null, md: "2026-08-27" },
          { d: null, st: "已回填", acct: "b-acct", views: 7 },
          { d: "2026-09-01", st: "待公开", acct: "c-acct", views: 999 },
        ],
      },
    ],
    [],
  );
  assert.equal(p.postCount, 3, "待公开那条不算已发");
  assert.equal(p.schedCount, 1);
  assert.equal(p.lastPostOn, "2026-08-30", "待公开的 09-01 不进最近发布日");
  assert.equal(p.firstPostOn, "2026-08-24");
  assert.equal(p.viewsTotal, 200, "只加有数的，且不算待公开那条的 999");
  assert.equal(p.viewsCount, 2);
  assert.deepEqual(p.accounts, ["a-acct", "b-acct"], "去重、排序、不含待公开那条的账号");
  assert.equal(p.metricAt, "2026-08-28");
  assert.deepEqual(p.sources, ["Reelshort榜单"]);
  assert.deepEqual(p.cats, ["爱情", "复仇"]);
  assert.deepEqual(p.who, ["高璇"]);
  assert.equal(p.createdOn, "2026-09-07");
  assert.equal(p.updatedOn, "2026-09-08");
  assert.equal(p.rowKeys.length, 0);
  assert.equal(p.titleKey, "x");
  const empty = summarizePosts([]);
  assert.deepEqual(empty, {
    postCount: 0,
    schedCount: 0,
    viewsTotal: 0,
    viewsCount: 0,
    firstPostOn: null,
    lastPostOn: null,
    accounts: [],
    metricAt: null,
  });
  /* 状态缺失的帖子不算已发：posted.py 的 published 集合是封闭的 */
  assert.equal(summarizePosts([{ d: "2026-08-01", views: 5 }]).postCount, 0);
});

test("账号台账：id 缺失用 name 顶，两个都没有的丢掉，重复 id 只留第一条", () => {
  const rows = toAccountRows([
    { id: "a1", name: "A", url: "https://www.tiktok.com/@a1", group: "A纯切片", form: "F", niche: "N", status: "未发", fans: 944, asOf: "2026-09-10" },
    { name: "only-name", fans: null },
    { id: "a1", name: "dup" },
    { url: "https://x" },
  ]);
  assert.equal(rows.length, 2);
  assert.deepEqual(rows[0], {
    id: "a1",
    name: "A",
    url: "https://www.tiktok.com/@a1",
    grp: "A纯切片",
    form: "F",
    niche: "N",
    status: "未发",
    fans: 944,
    asOf: "2026-09-10",
  });
  assert.equal(rows[1].id, "only-name");
  assert.equal(rows[1].fans, null);
  assert.equal(rows[1].asOf, null);
});

/*
 * 剧单导入的忙标记（feed v2 方案 4.3，P1-5）。行（约 42 次 upsert + 删旧批次）、信号（一个 batch 整表替换）、
 * 发布记录与账号（一个 batch）分三次提交，信号整表替换前后行数可能相同，只看 fingerprint 会把新行配上旧信号。
 * 所以 observe_sources 的 pick_catalog 必须在第一次写之前是 running、最后一次写（含写完核对行数）之后才 success，
 * 中途任一步抛错标 failed。写库阶段抽成 writeCatalogMarked，这里注入假的步骤记下先后。
 */
function fakeImport(opts: { throwAt?: string; failThrows?: boolean } = {}) {
  const calls: string[] = [];
  const logs: string[] = [];
  const boom = (name: string) => {
    calls.push(name);
    if (opts.throwAt === name) throw new Error(`${name} 炸了`);
  };
  const steps: CatalogWriteSteps<string> = {
    begin: async () => {
      boom("begin");
      return "att-1";
    },
    writeRows: async () => boom("rows"),
    writeSignals: async () => boom("signals"),
    writePosted: async () => boom("posted"),
    verify: async () => {
      boom("verify");
      return { rows: 3 };
    },
    complete: async (attempt, details) => boom(`complete:${attempt}:${JSON.stringify(details)}`),
    fail: async (attempt) => {
      calls.push(`fail:${attempt}`);
      if (opts.failThrows) throw new Error("标记也写不进去");
    },
    log: (line) => logs.push(line),
  };
  return { steps, calls, logs };
}

test("忙标记：第一次写之前 begin，最后一次写与写后核对之后才 complete（带核对出的行数）", async () => {
  const { steps, calls, logs } = fakeImport();
  await writeCatalogMarked(steps);
  assert.deepEqual(calls, ["begin", "rows", "signals", "posted", "verify", 'complete:att-1:{"rows":3}']);
  assert.deepEqual(logs, []);
});

test("忙标记：任一步抛错就标 failed（同一个 attempt），原错误原样抛出，后面的写不再执行", async () => {
  const order = ["rows", "signals", "posted", "verify"];
  for (const [i, step] of order.entries()) {
    const { steps, calls } = fakeImport({ throwAt: step });
    await assert.rejects(writeCatalogMarked(steps), new Error(`${step} 炸了`));
    assert.deepEqual(calls, ["begin", ...order.slice(0, i + 1), "fail:att-1"], step);
  }
  /* complete 自己失败（网络断在最后一步）也标 failed：不能让 running 一直挂着拦导出 */
  const { steps, calls } = fakeImport({ throwAt: 'complete:att-1:{"rows":3}' });
  await assert.rejects(writeCatalogMarked(steps));
  assert.deepEqual(calls.at(-1), "fail:att-1");
});

test("互斥：三次写库都拿到 begin 返回的那次 attempt（真实实现据此在每个 batch 第一句核验所有权）", async () => {
  const seen: string[] = [];
  const { steps } = fakeImport();
  await writeCatalogMarked({
    ...steps,
    writeRows: async (attempt) => void seen.push(`rows:${attempt}`),
    writeSignals: async (attempt) => void seen.push(`signals:${attempt}`),
    writePosted: async (attempt) => void seen.push(`posted:${attempt}`),
  });
  assert.deepEqual(seen, ["rows:att-1", "signals:att-1", "posted:att-1"]);
});

test("忙标记：begin 失败时一行都不写、也不标 failed；报错点名先执行迁移 SQL，且不带守门标记", async () => {
  const { steps, calls } = fakeImport({ throwAt: "begin" });
  await assert.rejects(writeCatalogMarked(steps), (error: Error) => {
    assert.match(error.message, /一行都没写/);
    assert.match(error.message, /scripts\/sql\/observe-source-pick-catalog\.sql/);
    assert.match(error.message, /begin 炸了/);
    /* 「不写库」是 preflight 守门的标记，notify 见到它会把人指向飞书选剧池；这里是库的问题，不能带 */
    assert.doesNotMatch(error.message, /不写库/);
    return true;
  });
  assert.deepEqual(calls, ["begin"]);
});

test("忙标记：标 failed 本身也失败时，仍抛原错误，另打一行说明 running 要 45 分钟后才按僵死处理", async () => {
  const { steps, calls, logs } = fakeImport({ throwAt: "signals", failThrows: true });
  await assert.rejects(writeCatalogMarked(steps), new Error("signals 炸了"));
  assert.deepEqual(calls, ["begin", "rows", "signals", "fail:att-1"]);
  assert.equal(logs.length, 1);
  assert.match(logs[0], /标记也写不进去/);
  assert.match(logs[0], /45 分钟/);
  assert.match(logs[0], /重跑 pnpm catalog-import/);
});

test("import-catalog.ts 的接线：守门与 --dry 都在 begin 之前，三步写库只经 writeCatalogMarked，标记的来源是 pick_catalog（源码文本合同）", () => {
  /* 这里只按源码钉先后与来源名；写库回调真的写了库、最后才标 success，由文件末尾的库内用例起子进程跑真实 CLI 来钉
     （只看源码字样时，三个写库回调换成空函数这条照样是绿的） */
  const src = readFileSync(new URL("../scripts/import-catalog.ts", import.meta.url), "utf8");
  const main = src.slice(src.indexOf("async function main()"));
  const at = (needle: string) => {
    const i = main.indexOf(needle);
    assert.ok(i >= 0, `main() 里找不到 ${needle}`);
    return i;
  };
  assert.ok(at("await preflight(") < at("if (dry)"));
  assert.ok(at("if (dry)") < at("await writeCatalogAtomic("));
  for (const call of ["await writeRows(", "await writeSignals(", "await writePosted("])
    assert.equal(main.split(call).length, 1, `main() 里不许绕过标记直接 ${call}`);
  assert.match(src, /beginSource\("pick_catalog"\)/);
  /* success 与所有权核验在同一个 batch（ownedBatch），被顶替时抛错而不是静悄悄更新 0 行 */
  assert.match(src, /ownedBatch\(attempt, \[getDb\(\)\.execute\(sourceSuccessSql\("pick_catalog", /);
  assert.match(src, /failSource\("pick_catalog", /);
});
