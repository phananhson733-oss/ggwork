import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";

import { EXPORT_VERSION, FORBIDDEN_NAME, PAN_SCRUB_REPLACEMENT, scrubPanText, toExportRow } from "../src/lib/pick/export-v2-map";
import type { ExportBody, ExportQuery, ExportResult } from "../src/lib/pick/export-v2-page";
import {
  FEED_COLUMNS,
  FEED_DEFAULT_LIMIT,
  FEED_VERSION,
  clipScrubbed,
  clipText,
  cutFeedPage,
  feedAuthorized,
  feedDate,
  feedPage,
  feedRulesMarkdown,
  parseFeedQuery,
  rowRef,
  splitTags,
  summarizePosted,
  toFeedRow,
  toFeedSourceRow,
  type FeedQuery,
  type FeedSourceRow,
} from "../src/lib/pick/feed-map";
import { handleExportGet, handleFeedGet, type ExportRouteDeps, type FeedResult, type FeedRouteDeps } from "../src/lib/pick/feed-http";

/** 一个补充平面字符（CJK 扩展 B），UTF-16 里占两个码元；按码元截断会把它劈成半个代理对 */
const ASTRAL = String.fromCodePoint(0x20bb7);
/** emoji 同样是补充平面字符，占两个码元 */
const EMOJI = String.fromCodePoint(0x1f600);
/** 孤立代理项经 UTF-8 往返会变成 U+FFFD，往返不等就是字符串里有半个代理对 */
const wellFormed = (s: string) => Buffer.from(s, "utf8").toString("utf8") === s;

const RS_ROW: FeedSourceRow = {
  rowKey: "reelshort-b1",
  platform: "reelshort",
  title: "Title",
  lang: "英语",
  tags: "",
  listedOn: null,
  offOn: null,
};

/** 路由测试的「现在」：as_of 必须不晚于它、不早于它 30 分钟 */
const NOW = new Date("2026-09-23T08:00:30.000Z");
const AS_OF_ISO = "2026-09-23T07:58:00.000Z";
const FP = "0123456789abcdef".repeat(4);
const P = PAN_SCRUB_REPLACEMENT;
const qs = (s: string) => new URLSearchParams(s);

test("feed 入参：缺省 limit、上限、坏值拒绝", () => {
  assert.deepEqual(parseFeedQuery(qs(""), NOW), { cursor: "", limit: FEED_DEFAULT_LIMIT, asOf: null, fp: null });
  assert.deepEqual(parseFeedQuery(qs("cursor=abc&limit=1000"), NOW), { cursor: "abc", limit: 1000, asOf: null, fp: null });
  for (const bad of ["limit=0", "limit=1001", "limit=-1", "limit=1e3", "limit=abc", "cursor=" + "x".repeat(513)])
    assert.equal(parseFeedQuery(qs(bad), NOW), null, bad);
});

test("feed 入参：as_of 与 fp 可选，校验同 v2（精确到分钟、不晚于 now、不早于 now − 30 分钟；fp 是 64 位小写十六进制）；带 fp 必须带 as_of", () => {
  const asOf = new Date(AS_OF_ISO);
  assert.deepEqual(parseFeedQuery(qs("as_of=2026-09-23T07:58:00Z"), NOW), { cursor: "", limit: FEED_DEFAULT_LIMIT, asOf, fp: null });
  assert.deepEqual(parseFeedQuery(qs(`as_of=2026-09-23T07:58Z&fp=${FP}&cursor=k&limit=2`), NOW), { cursor: "k", limit: 2, asOf, fp: FP });
  for (const bad of [
    "as_of=2026-09-23T07:58:30Z",
    "as_of=2026-09-23T08:01:00Z",
    "as_of=2026-09-23T07:30:00Z",
    "as_of=2026-09-23 07:58",
    "as_of=",
    "as_of=2026-02-30T07:58:00Z",
    `fp=${FP}`,
    `as_of=${AS_OF_ISO}&fp=${FP.toUpperCase()}`,
    `as_of=${AS_OF_ISO}&fp=abc`,
    `as_of=${AS_OF_ISO}&fp=`,
    `as_of=${AS_OF_ISO}&as_of=${AS_OF_ISO}`,
    `as_of=${AS_OF_ISO}&fp=${FP}&fp=${FP}`,
  ])
    assert.equal(parseFeedQuery(qs(bad), NOW), null, bad);
  /* 只增不改：不认识的参数与今天一样忽略，工作台现有的调用不受影响 */
  assert.deepEqual(parseFeedQuery(qs("x=1&cursor=a"), NOW), { cursor: "a", limit: FEED_DEFAULT_LIMIT, asOf: null, fp: null });
});

test("feed 鉴权：只认完整 Bearer，空 token 恒拒", () => {
  assert.equal(feedAuthorized("Bearer s3cret", "s3cret"), true);
  assert.equal(feedAuthorized("Bearer s3cre", "s3cret"), false);
  assert.equal(feedAuthorized("s3cret", "s3cret"), false);
  assert.equal(feedAuthorized(null, "s3cret"), false);
  assert.equal(feedAuthorized("Bearer ", ""), false);
});

test("日期只认合法的前 10 位", () => {
  assert.equal(feedDate("2026-09-20"), "2026-09-20");
  assert.equal(feedDate("2026-09-20 10:00:00+08"), "2026-09-20");
  assert.equal(feedDate("2026-13-40"), null);
  assert.equal(feedDate("W38"), null);
  assert.equal(feedDate(null), null);
});

test("日期必须是日历上真有的那天：V8 的 Date.parse 会把 2 月 30 日顺延到 3 月 2 日，不许当合法日期放行", () => {
  for (const bad of ["2026-02-30", "2026-04-31", "2026-02-29", "2100-02-29", "2026-06-31T08:00:00Z", "2026-00-10", "2026-09-00", "0000-01-01"])
    assert.equal(feedDate(bad), null, bad);
  for (const ok of ["2024-02-29", "2000-02-29", "2026-02-28", "2026-04-30", "2026-12-31"]) assert.equal(feedDate(ok), ok, ok);
});

test("tags 切分去重并截断", () => {
  assert.deepEqual(splitTags("复仇, 甜宠，复仇、总裁/ 豪门"), ["复仇", "甜宠", "总裁", "豪门"]);
  assert.equal(splitTags(Array.from({ length: 30 }, (_, i) => "t" + i).join(",")).length, 20);
});

test("tags：ReelShort 行是 SQL 用「 · 」拼成的一串，要切开；不带空格的「A·B」是一个标签", () => {
  assert.deepEqual(splitTags("Revenge · Billionaire · Werewolf"), ["Revenge", "Billionaire", "Werewolf"]);
  assert.deepEqual(splitTags("A·B · C"), ["A·B", "C"]);
  assert.deepEqual(splitTags("CEO · 复仇, 甜宠"), ["CEO", "复仇", "甜宠"]);
  assert.deepEqual(splitTags("A·B"), ["A·B"]);
});

test("截断以 UTF-16 码元为上限、只在码点边界下刀：标签 100、note 1000，不留半个代理对", () => {
  assert.equal(clipText("abc", 3), "abc");
  assert.equal(clipText("abcd", 3), "abc");
  assert.equal(clipText(ASTRAL + ASTRAL, 3), ASTRAL);
  assert.equal(clipText(ASTRAL + ASTRAL, 4), ASTRAL + ASTRAL);
  const [tag] = splitTags("a" + ASTRAL.repeat(100));
  assert.equal(tag, "a" + ASTRAL.repeat(49));
  assert.ok(wellFormed(tag));
  const note = (text: string) =>
    toFeedRow(RS_ROW, [{ kind: "kd", evidenceOn: null, rank: null, grade: "", note: text }], [], new Map()).signals[0].note;
  assert.equal(note("n".repeat(1500)), "n".repeat(1000));
  assert.equal(note(ASTRAL.repeat(600)), ASTRAL.repeat(500));
  /* 工作台前端 pickEvidenceSchema 的 note 是 zod z.string().max(1000)，按 .length（UTF-16 码元）数；
     按码点截到 1000 会放出 1999 个码元，一条就让整张候选列表解析失败 */
  const emoji = note("x" + EMOJI.repeat(999));
  assert.equal(emoji, "x" + EMOJI.repeat(499));
  for (const s of [emoji, note("n" + ASTRAL.repeat(1000))]) {
    assert.ok(s.length <= 1000, String(s.length));
    assert.ok(wellFormed(s));
  }
});

test("发布记录汇总：对不上是 matched=false，不是 0 条已发", () => {
  const none = summarizePosted([], new Map());
  assert.equal(none.matched, false);
  const sum = summarizePosted(
    [
      { sd: "SD-1", postCount: 2, schedCount: 0, lastPostOn: "2026-09-01" },
      { sd: "SD-2", postCount: 1, schedCount: 3, lastPostOn: "2026-09-10" },
    ],
    new Map([["SD-1", ["acc-a", " acc-b "]], ["SD-2", ["acc-a"]]]),
  );
  assert.deepEqual(sum, {
    matched: true,
    records: ["SD-1", "SD-2"],
    post_count: 3,
    sched_count: 3,
    last_post_on: "2026-09-10",
    accounts: ["acc-a", "acc-b"],
  });
});

test("行映射：语种、剧场、禁 YouTube、ReelShort 三个条件、不带网盘字段", () => {
  const row = toFeedRow(
    {
      rowKey: "kalos:123 ",
      platform: "reelshort",
      title: "Title",
      lang: "英语",
      tags: "",
      listedOn: "2026-09-01",
      offOn: null,
      rsFlags: { clk: true, bill: false, gsc: true },
    },
    [{ kind: "kd", evidenceOn: "2026-09-20", rank: 3, grade: "", note: "n" }],
    [],
    new Map(),
  );
  assert.equal(row.language, "en");
  assert.equal(row.theater, "ReelShort");
  assert.equal(Buffer.from(row.source_id, "base64url").toString(), "kalos:123 ");
  assert.deepEqual(row.signals.map((s) => s.kind), ["kd", "clk", "gsc"]);
  assert.equal(row.signals[0].rank, 3);
  assert.equal(row.signals[0].label, "KalosTV 日榜");
  /* 没带 rsFlagDates（不经 union 的调用方）时日期未知，不编一个 */
  assert.equal(row.signals[1].observed_at, null);
  assert.equal(row.posted.matched, false);
  assert.ok(!("panUrl" in row) && !("panPw" in row));
  assert.ok(!JSON.stringify(row).includes("pan"));
});

test("ReelShort 三个条件各带自己的证据日期，note 写明日期口径且声明不是指标数值", () => {
  const row = toFeedRow(
    {
      ...RS_ROW,
      rsFlags: { clk: true, bill: true, gsc: true },
      rsFlagDates: { clk: "2026-09-21", bill: "2026-09-19", gsc: "2026-09-15" },
    },
    [],
    [],
    new Map(),
  );
  const by = new Map(row.signals.map((s) => [s.kind, s]));
  assert.deepEqual([...by.keys()], ["clk", "bill", "gsc"]);
  assert.equal(by.get("clk")?.observed_at, "2026-09-21");
  assert.equal(by.get("bill")?.observed_at, "2026-09-19");
  assert.equal(by.get("gsc")?.observed_at, "2026-09-15");
  assert.match(by.get("clk")?.note ?? "", /最近一次过滤后出站/);
  assert.match(by.get("bill")?.note ?? "", /最近一个有订单的账单日/);
  assert.match(by.get("gsc")?.note ?? "", /GSC.*采集日/);
  for (const s of row.signals) {
    assert.match(s.note, /最近一次被看到/, s.kind);
    assert.match(s.note, /不表示指标数值/, s.kind);
    assert.equal(s.rank, null);
  }
  /* 条件没命中：日期在也不出信号；日期缺或是日历上没有的那天：observed_at 为 null，note 写日期未知 */
  const partial = toFeedRow(
    {
      ...RS_ROW,
      rsFlags: { clk: false, bill: true, gsc: true },
      rsFlagDates: { clk: "2026-09-21", bill: "2026-02-30", gsc: null },
    },
    [],
    [],
    new Map(),
  );
  assert.deepEqual(
    partial.signals.map((s) => [s.kind, s.observed_at]),
    [
      ["bill", null],
      ["gsc", null],
    ],
  );
  for (const s of partial.signals) {
    assert.match(s.note, /日期未知/, s.kind);
    assert.doesNotMatch(s.note, /最近一次被看到/, s.kind);
    assert.match(s.note, /不表示指标数值/, s.kind);
  }
});

test("行与信号的字段名与 pick-feed-v1 一致：接收方 extra=forbid，改形状必须升 FEED_VERSION", () => {
  assert.equal(FEED_VERSION, "pick-feed-v1");
  const row = toFeedRow(
    { ...RS_ROW, rsFlags: { clk: true, bill: false, gsc: false }, rsFlagDates: { clk: "2026-09-21", bill: null, gsc: null } },
    [{ kind: "kd", evidenceOn: "2026-09-20", rank: 1, grade: "", note: "n" }],
    [],
    new Map(),
  );
  assert.deepEqual(Object.keys(row).sort(), [
    "availability",
    "channel_rules",
    "detail_url",
    "language",
    "listed_at",
    "posted",
    "signals",
    "source",
    "source_id",
    "tags",
    "theater",
    "title",
  ]);
  assert.equal(row.signals.length, 2);
  for (const s of row.signals)
    assert.deepEqual(Object.keys(s).sort(), ["grade", "kind", "label", "note", "observed_at", "rank", "source_ref"]);
  assert.deepEqual(Object.keys(row.posted).sort(), ["accounts", "last_post_on", "matched", "post_count", "records", "sched_count"]);
  assert.deepEqual(Object.keys(row.channel_rules), ["youtube"]);
});

test("union 两支列对齐：ReelShort 三个条件的日期列在剧单支同位置给 NULL::text", () => {
  const src = readFileSync(join(process.cwd(), "src/lib/pick/queries-reelshort.ts"), "utf8");
  const body = (name: string) => {
    /* reelshortBranch 2026-09-23 起带可选的 asOf（feed v2 导出钉住时点），只按函数名找起点 */
    const start = src.indexOf(`export function ${name}(`);
    assert.ok(start >= 0, name);
    return src.slice(start, src.indexOf("\n}\n", start));
  };
  const tail = (b: string) => [...b.matchAll(/\bAS (rs_\w+|drama_id)\b/g)].map((m) => m[1]);
  const expected = ["rs_clk", "rs_bill", "rs_gsc", "rs_clk_on", "rs_bill_on", "rs_gsc_on", "drama_id"];
  const rs = body("reelshortBranch");
  const catalog = body("catalogBranch");
  assert.deepEqual(tail(rs), expected);
  assert.deepEqual(tail(catalog), expected);
  for (const c of ["rs_clk_on", "rs_bill_on", "rs_gsc_on"]) assert.ok(catalog.includes(`NULL::text AS ${c},`), c);
  assert.ok(rs.includes("c.last_on AS rs_clk_on"), "出站日期取观测台同一份 clicks7BySibling 的 last_on");
  assert.ok(rs.includes("b.last_on AS rs_bill_on"), "账单日期取观测台同一份 billBySibling 的 last_on");
  assert.ok(rs.includes("${gscOn()} AS rs_gsc_on"), "GSC 日期与 latest_evidence_on 共用 gscOn()");
  /* 三列要一路接到 toRowWithFlags，否则 feed 拿到的日期恒为 null */
  const flags = src.slice(src.indexOf("export function toRowWithFlags("));
  for (const k of ["clk", "bill", "gsc"]) assert.ok(flags.includes(`${k}: r.rs_${k}_on`), k);
});

test("feed 分页 SQL：按 row_key 升序多取一行交给 cutFeedPage，游标条件是 row_key > cursor", () => {
  const src = readFileSync(join(process.cwd(), "src/lib/pick/feed.ts"), "utf8");
  assert.ok(src.includes("rows.row_key > ${query.cursor}"));
  assert.ok(src.includes("ORDER BY rows.row_key LIMIT ${query.limit + 1}"));
  assert.match(src, /cutFeedPage\(.*, query\.limit\);/);
});

test("分页：取 limit+1 行，满页而没有多出来的那行时不给游标", () => {
  const rows = (keys: string[]) => keys.map((rowKey) => ({ rowKey }));
  const exact = cutFeedPage(rows(["a", "b", "c"]), 3);
  assert.deepEqual(exact.rows.map((r) => r.rowKey), ["a", "b", "c"]);
  assert.equal(exact.nextCursor, null);
  const more = cutFeedPage(rows(["a", "b", "c", "d"]), 3);
  assert.deepEqual(more.rows.map((r) => r.rowKey), ["a", "b", "c"]);
  assert.equal(more.nextCursor, "c");
  assert.deepEqual(cutFeedPage(rows(["a"]), 3), { rows: [{ rowKey: "a" }], nextCursor: null });
  assert.deepEqual(cutFeedPage([], 3), { rows: [], nextCursor: null });
});

test("分页：按 rowKey > cursor 走完整张有序键表，每行恰好一次、不漏不重", () => {
  const keys = [
    "dramabox:1",
    "dramabox:10",
    "dramabox:2",
    "kalos:123",
    "kalos:123 ",
    "kalos:1230",
    "reelshort-00a",
    "reelshort-0b",
    "sm:甜宠",
    "sm:甜宠 2",
  ].sort();
  for (const limit of [1, 2, 3, 4, keys.length - 1, keys.length, keys.length + 1]) {
    const seen: string[] = [];
    let cursor = "";
    let pages = 0;
    for (;;) {
      /* 模拟 loadFeedPage 的 SQL：WHERE row_key > cursor ORDER BY row_key LIMIT limit + 1 */
      const fetched = keys
        .filter((k) => !cursor || k > cursor)
        .slice(0, limit + 1)
        .map((rowKey) => ({ rowKey }));
      const page = cutFeedPage(fetched, limit);
      assert.ok(page.rows.length <= limit, `limit=${limit}`);
      seen.push(...page.rows.map((r) => r.rowKey));
      pages += 1;
      assert.ok(pages <= keys.length + 1, `limit=${limit}：游标不前进`);
      if (!page.nextCursor) break;
      assert.ok(page.nextCursor > cursor, `limit=${limit}：游标必须单调前进`);
      cursor = page.nextCursor;
    }
    assert.deepEqual(seen, keys, `limit=${limit}`);
    assert.equal(pages, Math.ceil(keys.length / limit), `limit=${limit}`);
  }
});

test("规则知识带采集时间与发布记录口径", () => {
  const md = feedRulesMarkdown("2026-09-23T00:00:00.000Z");
  assert.match(md, /采集时间：2026-09-23T00:00:00.000Z/);
  assert.match(md, /不等于「从未发布」/);
  assert.match(md, /## KalosTV/);
});

/* ---------- v1 增量（方案 4.6、4.7，P1-4）：行清洗、显式列、每页的 fingerprint ---------- */

const stripComments = (source: string) => source.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");
/** SELECT * 与 别名.*；count(*) 不在此列（同 tests/pick-export-v2-sql.test.ts） */
const STAR = /\bSELECT\s+(?:DISTINCT\s+)?\*|\b[A-Za-z_][A-Za-z0-9_]*\s*\.\s*\*/i;

test("v1 行输出前整行清洗网盘信息，与 v2 同一条 signal note 的清洗结果相同；source_id、detail_url、source_ref 按豁免清单原样", () => {
  const note = "榜单备注 https://pan.baidu.com/s/1AbCd?pwd=x7y8 提取码: x7y8 另见 https://youtube.com/@a";
  /* 这个 row_key 编进 detail_url 之后会被网盘正则命中（括号后面紧跟网盘域名）：不豁免就会被改掉 */
  const rowKey = "kalos-(pan.baidu.com/s/k)";
  assert.ok(scrubPanText(rowRef(rowKey)).hits > 0, "夹具本身要能被清洗命中，否则豁免没被测到");
  const row = toFeedRow(
    { ...RS_ROW, rowKey, platform: "kalos", title: "K 115.com/s/t", tags: "复仇, https://pan.baidu.com/s/abc/def" },
    [{ kind: "kd", evidenceOn: "2026-09-20", rank: 1, grade: "", note }],
    [{ sd: "SD-1", postCount: 1, schedCount: 0, lastPostOn: "2026-09-01" }],
    new Map([["SD-1", ["acct 密码=ab12"]]]),
  );
  const v2 = toExportRow("catalog_signals", {
    row_key: rowKey, kind: "kd", ord: 0, evidence_on: "2026-09-20", rank: 1, grade: "", note, payload: {},
  }).row;
  assert.equal(row.signals[0].note, P);
  assert.equal(row.signals[0].note, v2.note, "同一条备注在 v1 与 v2 的清洗结果必须相同，否则候选卡证据与资料页对不上");
  assert.equal(row.title, P);
  /* 先清洗、后切分：先按 / 切开会把分享路径 s、abc、def 留成标签；认出网盘片段就整串替换，同一格里的「复仇」一起丢 */
  assert.deepEqual(row.tags, [P]);
  assert.deepEqual(row.posted.accounts, [P]);
  assert.equal(Buffer.from(row.source_id, "base64url").toString(), rowKey);
  assert.equal(row.detail_url, rowRef(rowKey));
  for (const s of row.signals) assert.equal(s.source_ref, rowRef(rowKey));
  assert.equal(row.signals[0].observed_at, "2026-09-20");
});

test("v1 行清洗是先清洗、后截断：截到 1000 码元之前先整串替换，刀口不会切在网盘链接中间留下半截", () => {
  const long = "x".repeat(989) + " https://pan.baidu.com/s/abcdef";
  assert.equal(long.length, 1020);
  const note = toFeedRow(RS_ROW, [{ kind: "kd", evidenceOn: null, rank: null, grade: "", note: long }], [], new Map()).signals[0].note;
  assert.equal(note, P);
  /* 反过来（先截后洗）会留下 https://pa：域名被截断，正则认不出来 */
  assert.equal(scrubPanText(clipText(long, 1000)).text, "x".repeat(989) + " https://pa");
});

test("v1 截断不会新露出网盘片段：截断后再清洗，截出来的命中整串换成占位符；note 不超 1000、标签不超 100，工作台闸门不会整批拒收", () => {
  const noteOf = (text: string) =>
    toFeedRow(RS_ROW, [{ kind: "kd", evidenceOn: null, rank: null, grade: "", note: text }], [], new Map()).signals[0].note;
  /* 9 位的码整段认不出（码是 4–8 位）；截到 1000 后只剩 6 位，就成了一个提取码 */
  const codeTail = "中".repeat(990) + "提取码:abcd12345";
  assert.equal(scrubPanText(codeTail).hits, 0);
  assert.equal(noteOf(codeTail), P);
  const grows = "中".repeat(993) + "密码:abcdefghij";
  assert.equal(scrubPanText(grows).hits, 0);
  assert.equal(noteOf(grows), P);
  /* 域名右边界：115.comx 整段不算，截掉 x 就是 115.com */
  const domain = "中".repeat(993) + "115.comx";
  assert.equal(scrubPanText(domain).hits, 0);
  assert.equal(noteOf(domain), P);
  /* 标签逐个截到 100，同一套规则；经 toFeedRow 的整行清洗之后也不超 100 */
  const tagText = "中".repeat(93) + "密码:abcdefgh1";
  assert.equal(scrubPanText(tagText).hits, 0);
  assert.deepEqual(splitTags(tagText), [P]);
  assert.deepEqual(toFeedRow({ ...RS_ROW, tags: tagText }, [], [], new Map()).tags, [P]);
  /* 占位符比 max 长时再截一次，截下来的半截占位符不会再命中 */
  assert.equal(clipScrubbed("密码:abcd", 7), P.slice(0, 7));
});

test("v1 截断后的清洗循环到干净为止：串联的网盘片段、截断造出的命中都整串换成一个占位符，不留网盘命中", () => {
  /* 每一段的左边紧贴上一段的端口号 9 */
  const chain = "pan.baidu.com:9".repeat(5) + "pan.baidu.com/s/1TitleLeak";
  assert.equal(clipScrubbed(chain, 1000), P);
  const note = toFeedRow(RS_ROW, [{ kind: "kd", evidenceOn: null, rank: null, grade: "", note: chain }], [], new Map()).signals[0].note;
  assert.equal(note, P);
  assert.equal(scrubPanText(note).hits, 0);
  /* 截断造出的命中照样清到干净：刀口切掉 x 之后尾巴是 115.com */
  const clipped = clipScrubbed("pan.baidu.com:9".repeat(4) + "中".repeat(33) + "115.comx", 100);
  assert.equal(clipped, P);
  assert.equal(scrubPanText(clipped).hits, 0, clipped);
  assert.deepEqual(toFeedRow({ ...RS_ROW, tags: chain }, [], [], new Map()).tags, [P]);
});

test("v1 显式列：feed.ts 不再 SELECT rows.*，只取 toFeedRow 用到的列，没有网盘与金额列", () => {
  assert.deepEqual(
    [...FEED_COLUMNS],
    ["row_key", "platform", "title", "lang", "tags", "listed_on", "off_on", "rs_clk", "rs_bill", "rs_gsc", "rs_clk_on", "rs_bill_on", "rs_gsc_on"],
  );
  for (const c of FEED_COLUMNS) assert.doesNotMatch(c, FORBIDDEN_NAME, c);
  const code = stripComments(readFileSync(join(process.cwd(), "src/lib/pick/feed.ts"), "utf8"));
  assert.doesNotMatch(code, /rows\.\*/);
  assert.doesNotMatch(code, STAR);
  assert.match(code, /FEED_COLUMNS/);
  assert.match(code, /toFeedSourceRow/);
  /* 只读这几列：查询结果里即使多出网盘列也不会带进行对象；剧场不认识时与 toRow 一样落到 dramabox */
  const src = toFeedSourceRow({
    row_key: "k1", platform: "nope", title: "T", lang: "英语", tags: "a", listed_on: "2026-09-01", off_on: null,
    rs_clk: true, rs_bill: false, rs_gsc: true, rs_clk_on: "2026-09-02", rs_bill_on: null, rs_gsc_on: "2026-09-03",
    pan_url: "https://pan.baidu.com/s/SENTINEL", pan_pw: "zz9q",
  });
  assert.deepEqual(src, {
    rowKey: "k1",
    platform: "dramabox",
    title: "T",
    lang: "英语",
    tags: "a",
    listedOn: "2026-09-01",
    offOn: null,
    rsFlags: { clk: true, bill: false, gsc: true },
    rsFlagDates: { clk: "2026-09-02", bill: null, gsc: "2026-09-03" },
  });
  assert.equal(toFeedSourceRow({ row_key: "k2", platform: "kalos", title: "T", lang: "", tags: "", listed_on: null, off_on: "2026-09-09" }).platform, "kalos");
});

test("v1 响应顶层只新增 fingerprint，而且每一页都带（不只是首页）；其余顶层键不变", () => {
  const V1_KEYS = ["capturedAt", "freshness", "nextCursor", "rows", "rules", "scope", "sourceRevision", "total", "version"];
  const first = feedPage({
    capturedAt: AS_OF_ISO,
    meta: { total: 3, freshness: { catalogRows: 3 }, rules: "# 规则" },
    sourceRevision: "abc123",
    rows: [],
    nextCursor: "k",
    fingerprint: FP,
  });
  const next = feedPage({ capturedAt: AS_OF_ISO, meta: null, sourceRevision: "abc123", rows: [], nextCursor: null, fingerprint: FP });
  for (const page of [first, next]) {
    assert.deepEqual(Object.keys(page).sort(), [...V1_KEYS, "fingerprint"].sort());
    assert.equal(page.fingerprint, FP);
    assert.equal(page.version, FEED_VERSION);
    assert.equal(page.capturedAt, AS_OF_ISO);
    assert.equal(page.sourceRevision, "abc123");
  }
  assert.deepEqual([first.total, first.freshness, first.rules], [3, { catalogRows: 3 }, "# 规则"]);
  assert.deepEqual([next.total, next.freshness, next.rules], [null, null, null]);
});

/* ---------- 两条路由的状态码矩阵（方案 4.1、4.7，P1-4）：假的 load，不连库 ---------- */

const TOKEN = "s3cret-token";

function authHeaders(auth: string | null): HeadersInit {
  return auth === null ? {} : { authorization: auth };
}

const feedReq = (query = "", auth: string | null = `Bearer ${TOKEN}`) =>
  new Request(`https://x.test/api/pick-feed${query ? `?${query}` : ""}`, { headers: authHeaders(auth) });

/** token 放在对象里传：直接写成带缺省值的参数，传 undefined 会落回缺省值，「没配 token」就测不到 */
function feedDeps(result: FeedResult | Error, { token }: { token: string | undefined } = { token: TOKEN }) {
  const calls: FeedQuery[] = [];
  const deps: FeedRouteDeps = {
    token,
    now: NOW,
    load: async (q) => {
      calls.push(q);
      if (result instanceof Error) throw result;
      return result;
    },
  };
  return { deps, calls };
}

const exportReq = (resource: string, query: string, auth: string | null = `Bearer ${TOKEN}`) =>
  new Request(`https://x.test/api/pick-feed/v2/${resource}?${query}`, { headers: authHeaders(auth) });

function exportDeps(result: ExportResult | Error, { token }: { token: string | undefined } = { token: TOKEN }) {
  const calls: ExportQuery[] = [];
  const deps: ExportRouteDeps = {
    token,
    now: NOW,
    load: async (q) => {
      calls.push(q);
      if (result instanceof Error) throw result;
      return result;
    },
  };
  return { deps, calls };
}

/** 截下 console.error：读失败只许打一行固定文字，不带异常内容 */
async function withErrorLog<T>(fn: () => Promise<T>): Promise<{ value: T; logged: unknown[][] }> {
  const original = console.error;
  const logged: unknown[][] = [];
  console.error = (...args: unknown[]) => {
    logged.push(args);
  };
  try {
    return { value: await fn(), logged };
  } finally {
    console.error = original;
  }
}

async function expectJson(res: Response, status: number, body: unknown, what: string) {
  assert.equal(res.status, status, what);
  assert.equal(res.headers.get("cache-control"), "no-store", `${what}：每个响应都不许缓存`);
  assert.deepEqual(await res.json(), body, what);
}

const PAGE = feedPage({ capturedAt: AS_OF_ISO, meta: null, sourceRevision: "abc123", rows: [], nextCursor: null, fingerprint: FP });

test("v1 路由状态码矩阵：未配 token 404、Bearer 不对 401、参数错 400、409 source_changed、503 source_busy 带 Retry-After、读失败 503 只打固定日志", async () => {
  const ok: FeedResult = { status: 200, page: PAGE };
  for (const token of [undefined, "", "  "]) {
    const { deps, calls } = feedDeps(ok, { token });
    await expectJson(await handleFeedGet(feedReq(), deps), 404, { ok: false, error: "not_found" }, `token=${JSON.stringify(token)}`);
    assert.equal(calls.length, 0);
  }
  /* Fetch 会去掉头值两端的空白，所以「多一个尾随空格」测不出来；换成多一个字符 */
  for (const auth of [null, "Bearer wrong", TOKEN, `Bearer ${TOKEN}x`, `bearer ${TOKEN}`]) {
    const { deps, calls } = feedDeps(ok);
    await expectJson(await handleFeedGet(feedReq("", auth), deps), 401, { ok: false, error: "unauthorized" }, `auth=${auth}`);
    assert.equal(calls.length, 0);
  }
  for (const bad of ["limit=0", `fp=${FP}`, "as_of=2026-09-23T07:00:00Z", `as_of=${AS_OF_ISO}&fp=zz`]) {
    const { deps, calls } = feedDeps(ok);
    await expectJson(await handleFeedGet(feedReq(bad), deps), 400, { ok: false, error: "bad_request" }, bad);
    assert.equal(calls.length, 0, bad);
  }

  const good = feedDeps(ok);
  await expectJson(
    await handleFeedGet(feedReq(`as_of=2026-09-23T07:58:00Z&fp=${FP}&cursor=k&limit=2`), good.deps),
    200,
    JSON.parse(JSON.stringify({ ok: true, ...PAGE })),
    "200",
  );
  assert.deepEqual(good.calls, [{ cursor: "k", limit: 2, asOf: new Date(AS_OF_ISO), fp: FP }]);
  /* 不带 as_of / fp：与今天一样的请求，load 收到的两项都是 null（不核 fingerprint） */
  const plain = feedDeps(ok);
  assert.equal((await handleFeedGet(feedReq(), plain.deps)).status, 200);
  assert.deepEqual(plain.calls, [{ cursor: "", limit: FEED_DEFAULT_LIMIT, asOf: null, fp: null }]);

  await expectJson(
    await handleFeedGet(feedReq(`as_of=${AS_OF_ISO}&fp=${FP}`), feedDeps({ status: 409, error: "source_changed" }).deps),
    409,
    { ok: false, error: "source_changed" },
    "409",
  );
  const busy = await handleFeedGet(feedReq(`as_of=${AS_OF_ISO}&fp=${FP}`), feedDeps({ status: 503, error: "source_busy", retryAfter: 60 }).deps);
  assert.equal(busy.headers.get("retry-after"), "60");
  await expectJson(busy, 503, { ok: false, error: "source_busy" }, "503 source_busy");

  const { value: failed, logged } = await withErrorLog(() => handleFeedGet(feedReq(), feedDeps(new Error("connect password=hunter2 host=db.internal")).deps));
  await expectJson(failed, 503, { ok: false, error: "read_failed" }, "503 read_failed");
  assert.deepEqual(logged, [["[pick-feed] read failed"]], "只打印固定日志，不带异常细节");
});

test("v2 路由状态码矩阵：404、401、400（带固定的原因词）、200、409、503 source_busy 带 Retry-After、500 row_too_large、503 read_failed；每个响应都带 version", async () => {
  const V = EXPORT_VERSION;
  const body: ExportBody = {
    ok: true,
    version: V,
    resource: "catalog_rows",
    asOf: AS_OF_ISO,
    fingerprint: FP,
    rows: [{ row_key: "k" }],
    nextCursor: null,
  };
  const ok: ExportResult = { status: 200, body, hits: { "catalog_rows.title": 1 } };
  const rowsQuery = `as_of=${AS_OF_ISO}&fp=${FP}`;
  for (const token of [undefined, "", " "]) {
    const { deps, calls } = exportDeps(ok, { token });
    await expectJson(await handleExportGet(exportReq("catalog_rows", rowsQuery), "catalog_rows", deps), 404, { ok: false, version: V, error: "not_found" }, "404");
    assert.equal(calls.length, 0);
  }
  for (const auth of [null, "Bearer wrong", TOKEN]) {
    const { deps, calls } = exportDeps(ok);
    await expectJson(
      await handleExportGet(exportReq("catalog_rows", rowsQuery, auth), "catalog_rows", deps),
      401,
      { ok: false, version: V, error: "unauthorized" },
      `auth=${auth}`,
    );
    assert.equal(calls.length, 0);
  }
  for (const [resource, query, reason] of [
    ["nope", rowsQuery, "resource"],
    ["catalog_rows", `as_of=${AS_OF_ISO}`, "fp"],
    ["manifest", "", "as_of"],
    ["manifest", `as_of=2026-09-23T07:00Z`, "as_of"],
    ["manifest", rowsQuery, "unknown_param"],
    ["catalog_rows", `${rowsQuery}&limit=5001`, "limit"],
    ["catalog_rows", `${rowsQuery}&cursor=bm9wZQ`, "cursor"],
    ["rs_series_day", rowsQuery, "day"],
  ] as const) {
    const { deps, calls } = exportDeps(ok);
    await expectJson(await handleExportGet(exportReq(resource, query), resource, deps), 400, { ok: false, version: V, error: "bad_request", reason }, `${resource}?${query}`);
    assert.equal(calls.length, 0);
  }

  const good = exportDeps(ok);
  await expectJson(await handleExportGet(exportReq("catalog_rows", `${rowsQuery}&limit=2`), "catalog_rows", good.deps), 200, body, "200：正文就是 body，清洗命中数不进正文");
  assert.equal(good.calls.length, 1);
  assert.deepEqual(good.calls[0], { resource: "catalog_rows", asOf: new Date(AS_OF_ISO), fp: FP, cursor: null, limit: 2, day: null });
  const manifest = exportDeps({ status: 200, body: { ...body, resource: "manifest" }, hits: {} });
  assert.equal((await handleExportGet(exportReq("manifest", `as_of=${AS_OF_ISO}`), "manifest", manifest.deps)).status, 200);
  assert.deepEqual(manifest.calls[0], { resource: "manifest", asOf: new Date(AS_OF_ISO), fp: null, cursor: null, limit: 1, day: null });

  await expectJson(
    await handleExportGet(exportReq("catalog_rows", rowsQuery), "catalog_rows", exportDeps({ status: 409, error: "source_changed" }).deps),
    409,
    { ok: false, version: V, error: "source_changed" },
    "409",
  );
  const busy = await handleExportGet(exportReq("manifest", `as_of=${AS_OF_ISO}`), "manifest", exportDeps({ status: 503, error: "source_busy", retryAfter: 60 }).deps);
  assert.equal(busy.headers.get("retry-after"), "60");
  await expectJson(busy, 503, { ok: false, version: V, error: "source_busy" }, "503 source_busy");
  await expectJson(
    await handleExportGet(
      exportReq("catalog_accounts", rowsQuery),
      "catalog_accounts",
      exportDeps({ status: 500, error: "row_too_large", resource: "catalog_accounts", key: ["acct-big"] }).deps,
    ),
    500,
    { ok: false, version: V, error: "row_too_large", resource: "catalog_accounts", key: ["acct-big"] },
    "500 row_too_large 只带资源名与主键",
  );
  const { value: failed, logged } = await withErrorLog(() =>
    handleExportGet(exportReq("catalog_rows", rowsQuery), "catalog_rows", exportDeps(new Error("relation catalog_rows password=hunter2")).deps),
  );
  await expectJson(failed, 503, { ok: false, version: V, error: "read_failed" }, "503 read_failed");
  assert.deepEqual(logged, [["[pick-feed-v2] read failed"]], "只打印固定日志，不带异常细节");
});

test("两条路由都是薄壳：只读各自的 token 与当前时刻，鉴权、参数与状态码都在 feed-http；v2 是 force-dynamic、maxDuration 60", () => {
  const read = (p: string) => stripComments(readFileSync(join(process.cwd(), p), "utf8"));
  const v2 = read("src/app/api/pick-feed/v2/[resource]/route.ts");
  assert.match(v2, /^export const dynamic = "force-dynamic";$/m);
  assert.match(v2, /^export const maxDuration = 60;$/m);
  assert.match(v2, /process\.env\.PICK_EXPORT_TOKEN/);
  assert.doesNotMatch(v2, /PICK_FEED_TOKEN/, "v1 的 token 不许开 v2");
  assert.match(v2, /handleExportGet\(request, resource, \{/);
  assert.match(v2, /loadExportPage\(query, exportContext\(now\)\)/);
  assert.doesNotMatch(v2, /export (async )?(function|const) (POST|PUT|PATCH|DELETE)/);
  const v1 = read("src/app/api/pick-feed/route.ts");
  assert.match(v1, /^export const dynamic = "force-dynamic";$/m);
  assert.match(v1, /^export const maxDuration = 60;$/m);
  assert.match(v1, /process\.env\.PICK_FEED_TOKEN/);
  assert.doesNotMatch(v1, /PICK_EXPORT_TOKEN/, "v2 的 token 不许开 v1");
  assert.match(v1, /handleFeedGet\(request, \{/);
  assert.match(v1, /loadFeedPage\(query, feedContext\(now\)\)/);
  /* 纯外壳不许 import server-only 的东西：pnpm test 直接加载它 */
  const http = readFileSync(join(process.cwd(), "src/lib/pick/feed-http.ts"), "utf8");
  assert.doesNotMatch(http, /^import\s+["']server-only["']/m);
  assert.doesNotMatch(http, /from "(?:@\/lib\/pick|\.)\/(?:export-v2|feed)"/);
  assert.doesNotMatch(http, /@\/db/);
});
