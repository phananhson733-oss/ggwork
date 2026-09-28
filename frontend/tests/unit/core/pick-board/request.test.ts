// PORTED_FROM: realshort@816ca2e tests/pick-request.test.ts
// 本地改动：node:test 换成 @rstest/core；queyuHref 从拆出去的 queyu.ts 取。
import assert from "node:assert/strict";

import { test } from "@rstest/core";

import { queyuHref } from "@/core/pick-board/queyu";
import {
  BASES,
  BASIS_DATE_LABEL,
  BASIS_LABELS,
  DAILY_RANKS,
  GRADES,
  MAX_PAGE,
  PAGE_SIZE,
  PLATFORMS,
  PLATFORM_LABELS,
  POSTED_STATES,
  POSTED_STATE_LABELS,
  RANK_DEFAULT,
  RANK_LABELS,
  RS_RANKS,
  isRsBasis,
  parsePickRequest,
  pickQuery,
  reelshortId,
  reelshortRowKey,
} from "@/core/pick-board/request";

test("坏参数一律回落默认，不抛", () => {
  const r = parsePickRequest(
    new URLSearchParams(
      "tab=;drop&sort=;drop table&platform=reelshort&basis=xx&posted=maybe&page=-3&size=100000&lang=en;--&row=../x",
    ),
  );
  assert.equal(r.tab, "pick");
  assert.equal(r.sort, "evidence");
  assert.equal(
    r.platform,
    "reelshort",
    "2026-09-11 起 ReelShort 是第十个剧场，platform=reelshort 合法",
  );
  assert.equal(
    parsePickRequest(new URLSearchParams("platform=netflix")).platform,
    "",
    "不在白名单的平台回落",
  );
  assert.equal(r.basis, "");
  assert.equal(r.posted, "");
  assert.equal(r.page, 1);
  assert.equal(r.size, PAGE_SIZE, "不在白名单的 size 回落");
  assert.equal(r.lang, "", "语种只收字母与汉字");
  assert.equal(
    r.rowKey,
    "../x",
    "row_key 只做等值查询，不限字符集（GoodShort 的 id 是 base64）",
  );
  assert.equal(
    parsePickRequest(new URLSearchParams("row=%01abc")).rowKey,
    "",
    "控制字符拒绝",
  );
  assert.equal(
    parsePickRequest(new URLSearchParams(`row=${"a".repeat(121)}`)).rowKey,
    "",
    "超长拒绝",
  );
  assert.equal(
    parsePickRequest(new URLSearchParams("row=shortmax-845227（已设置定时）"))
      .rowKey,
    "shortmax-845227（已设置定时）",
  );
});

test("row 原样往返：首尾空格是行键的一部分，不 trim；全空白、控制字符、超长照旧拒", () => {
  /* 2026-09-13 只读实测：catalog_rows 有 19 个 ShortMax 行键带尾随空格（shortmax-856049 ），12 个在选剧 tab 默认可见。
     trim 之后是另一个键，等值查询永远查不到——列表里点剧名进证据页落「找不到这一行」，get_catalog_row 同样找不到 */
  const base = parsePickRequest(new URLSearchParams(""));
  for (const key of [
    "shortmax-856049 ",
    " kalos-1",
    "goodshort-6NXGT8qQEzUa0Fh4NEshqQ== 这",
    "mqk++n/L+Wf/xDC0G43CRQ==",
  ]) {
    const q = pickQuery(base, { tab: "row", rowKey: key });
    assert.equal(
      parsePickRequest(new URLSearchParams(q.slice(1))).rowKey,
      key,
      `${JSON.stringify(key)} 往返后要逐字相等`,
    );
  }
  assert.equal(
    parsePickRequest(new URLSearchParams("row=shortmax-856049+")).rowKey,
    "shortmax-856049 ",
    "+ 解码成空格，原样保留",
  );
  for (const bad of [
    "row=",
    "row=+",
    "row=%20%20",
    "row=%E3%80%80",
    "row=%09",
    "row=shortmax-%0A843464",
  ])
    assert.equal(
      parsePickRequest(new URLSearchParams(bad)).rowKey,
      "",
      `${bad} 应被拒`,
    );
  assert.equal(
    parsePickRequest(new URLSearchParams(`row=${"a".repeat(119)}+`)).rowKey
      .length,
    120,
    "长度按原样算，尾随空格也计入",
  );
  assert.equal(
    parsePickRequest(new URLSearchParams(`row=${"a".repeat(120)}+`)).rowKey,
    "",
    "加上空格超长同样拒",
  );
});

test("合法参数原样进来；页码封顶；对象形态的 searchParams 也认", () => {
  const r = parsePickRequest({
    tab: "all",
    sort: "title",
    platform: "kalos",
    basis: "kd",
    posted: "no",
    off: "1",
    sig: "1",
    page: [String(Number.MAX_SAFE_INTEGER), "2"],
    size: "200",
    lang: "繁体中文",
    q: "  dragon\u0000 contract  ",
    row: "kalos-the-dragon-s-contract-x",
  });
  assert.equal(r.tab, "all");
  assert.equal(r.sort, "title");
  assert.equal(r.platform, "kalos");
  assert.equal(r.basis, "kd");
  assert.equal(r.posted, "no");
  assert.equal(r.withOff, true);
  assert.equal(r.signalOnly, true);
  assert.equal(r.page, MAX_PAGE, "数组取第一个，且封顶");
  assert.equal(r.size, 200);
  assert.equal(r.lang, "繁体中文");
  assert.equal(r.q, "dragon contract", "控制字符去掉、首尾裁剪");
  assert.equal(r.rowKey, "kalos-the-dragon-s-contract-x");
});

test("搜索词有长度上限", () => {
  const r = parsePickRequest(new URLSearchParams(`q=${"a".repeat(500)}`));
  assert.equal(r.q.length, 80);
});

test("查询串只写非默认值，来回一致", () => {
  const base = parsePickRequest(new URLSearchParams(""));
  assert.equal(pickQuery(base), "");
  const q = pickQuery(base, {
    tab: "all",
    platform: "shortmax",
    page: 3,
    size: 100,
    q: "龙",
  });
  assert.equal(q, "?tab=all&platform=shortmax&q=%E9%BE%99&size=100&page=3");
  const back = parsePickRequest(new URLSearchParams(q));
  assert.deepEqual(back, {
    ...base,
    tab: "all",
    platform: "shortmax",
    page: 3,
    size: 100,
    q: "龙",
  });
  // 证据页才写 row；别的 tab 不带
  assert.equal(
    pickQuery(base, { tab: "row", rowKey: "k-1" }),
    "?tab=row&row=k-1",
  );
  assert.equal(pickQuery(base, { tab: "pick", rowKey: "k-1" }), "");
});

test("依据与剧场的标签表与白名单一一对应", () => {
  for (const b of BASES) assert.ok(BASIS_LABELS[b], `缺 ${b} 的标签`);
  for (const p of PLATFORMS) assert.ok(PLATFORM_LABELS[p], `缺 ${p} 的标签`);
  assert.equal(Object.keys(BASIS_LABELS).length, BASES.length);
  assert.equal(Object.keys(PLATFORM_LABELS).length, PLATFORMS.length);
});

test("鹊娱两张榜是信号种类：与 KalosTV 日榜同属按日出榜，证据日期叫榜单日期，basis / rk 都能往返", () => {
  for (const k of ["qc", "qr"] as const) {
    assert.ok((BASES as readonly string[]).includes(k), `${k} 不在 BASES`);
    assert.equal(BASIS_DATE_LABEL[k], "榜单日期");
    assert.ok(
      DAILY_RANKS.has(k),
      `${k} 要走日榜逻辑（payload.h 是 [日期, 名次]）`,
    );
    assert.match(BASIS_LABELS[k], /鹊娱/);
    assert.equal(parsePickRequest({ basis: k }).basis, k);
    const req = parsePickRequest({ tab: "rank", rk: k, day: "2026-09-11" });
    assert.equal(req.rank, k);
    assert.equal(req.day, "2026-09-11");
    assert.equal(new URLSearchParams(pickQuery(req)).get("rk"), k);
  }
  assert.ok(DAILY_RANKS.has("kd") && !DAILY_RANKS.has("kw"), "周榜不是日榜");
});

test("鹊娱地址只把剧名带到列表页上，剧名要编码", () => {
  assert.equal(
    queyuHref("龙之契约 & Co"),
    "https://cps-distribution.zwnet.cn/promotion/index?title=%E9%BE%99%E4%B9%8B%E5%A5%91%E7%BA%A6%20%26%20Co",
  );
});

test("榜单 tab 的入参：坏 rk / grade 回落，day 只收 YYYY-MM-DD，week 拒控制字符与超长", () => {
  const bad = parsePickRequest(
    new URLSearchParams(
      "tab=rank&rk=xx&grade=Z&day=2026-13-99x&week=%01w&pst=maybe&sd=../x",
    ),
  );
  assert.equal(bad.tab, "rank");
  assert.equal(bad.rank, RANK_DEFAULT, "不在 BASES 里的榜回落默认日榜");
  assert.equal(bad.grade, "", "不在 GRADES 里的评级回落");
  assert.equal(bad.day, "", "日期形状不对就当没传（查询层再回落到最近一天）");
  assert.equal(bad.week, "", "周标签拒控制字符");
  assert.equal(bad.postedState, "");
  assert.equal(bad.sd, "", "记录编号只收字母数字与 - _");
  assert.equal(
    parsePickRequest(new URLSearchParams("day=2026-13-99")).day,
    "2026-13-99",
    "只校形状，不校日历；不存在的日期由查询层回落",
  );
  assert.equal(
    parsePickRequest(new URLSearchParams(`week=${"w".repeat(25)}`)).week,
    "",
    "周标签超长拒绝",
  );
  const ok = parsePickRequest(
    new URLSearchParams(
      "tab=rank&rk=sm&grade=SS&day=2026-09-10&week=9%2F1-9%2F7&pst=nomatch&sd=SD-000001",
    ),
  );
  assert.equal(ok.rank, "sm");
  assert.equal(ok.grade, "SS");
  assert.equal(ok.day, "2026-09-10");
  assert.equal(ok.week, "9/1-9/7");
  assert.equal(ok.postedState, "nomatch");
  assert.equal(ok.sd, "SD-000001");
  for (const g of GRADES) assert.ok(g.length <= 3);
  for (const s of POSTED_STATES)
    assert.ok(s in POSTED_STATE_LABELS, `缺 ${s || "(全部)"} 的标签`);
});

test("榜单 / 发布记录的参数只在各自的 tab 下写进查询串，来回一致", () => {
  const base = parsePickRequest(new URLSearchParams(""));
  const rank = pickQuery(base, {
    tab: "rank",
    rank: "sm",
    grade: "SS",
    day: "2026-09-10",
    postedState: "pub",
    sd: "SD-1",
  });
  assert.equal(
    rank,
    "?tab=rank&rk=sm&day=2026-09-10&grade=SS",
    "pst / sd 不属于榜单 tab",
  );
  assert.deepEqual(parsePickRequest(new URLSearchParams(rank)), {
    ...base,
    tab: "rank",
    rank: "sm",
    grade: "SS",
    day: "2026-09-10",
  });
  assert.equal(
    pickQuery(base, { tab: "rank" }),
    "?tab=rank",
    "默认日榜不写 rk",
  );
  const posted = pickQuery(base, {
    tab: "posted",
    postedState: "nomatch",
    sd: "SD-000001",
    rank: "kw",
    page: 2,
  });
  assert.equal(
    posted,
    "?tab=posted&page=2&pst=nomatch&sd=SD-000001",
    "rk 不属于发布记录 tab",
  );
  assert.deepEqual(parsePickRequest(new URLSearchParams(posted)), {
    ...base,
    tab: "posted",
    postedState: "nomatch",
    sd: "SD-000001",
    page: 2,
  });
});

test("ReelShort 榜的子参数只在 rs 榜下写，坏值回落；四个开关来回一致", () => {
  const base = parsePickRequest(new URLSearchParams(""));
  const rs = pickQuery(base, {
    tab: "rank",
    rank: "rs_growth",
    rsSort: "d7",
    rsLocale: "en",
    rsBucket: "0-7",
    grade: "SS",
    day: "2026-09-10",
  });
  assert.equal(
    rs,
    "?tab=rank&rk=rs_growth&rs=d7&rl=en&bk=0-7",
    "day / grade 不属于 rs 榜；rs 参数只在 rs 榜下写",
  );
  assert.deepEqual(parsePickRequest(new URLSearchParams(rs)), {
    ...base,
    tab: "rank",
    rank: "rs_growth",
    rsSort: "d7",
    rsLocale: "en",
    rsBucket: "0-7",
  });
  const theater = pickQuery(base, {
    tab: "rank",
    rank: "kw",
    rsSort: "d7",
    rsLocale: "en",
    rsBucket: "0-7",
    week: "9/1-9/7",
  });
  assert.equal(
    theater,
    "?tab=rank&rk=kw&week=9%2F1-9%2F7",
    "剧场榜下不写 rs 参数",
  );
  const bad = parsePickRequest(
    new URLSearchParams("tab=rank&rk=rs_zzz&rs=;drop&rl=en;--&bk=never"),
  );
  assert.equal(bad.rank, RANK_DEFAULT, "不存在的榜回落默认日榜");
  assert.equal(bad.rsSort, "rr");
  assert.equal(bad.rsLocale, "");
  assert.equal(bad.rsBucket, null);
  assert.equal(
    parsePickRequest(new URLSearchParams("rl=zh-hant")).rsLocale,
    "zh-hant",
    "带地区段的语种码合法",
  );
  for (const k of RS_RANKS) assert.ok(k in RANK_LABELS, `缺 ${k} 的标签`);
  for (const b of ["clk", "bill", "gsc"] as const)
    assert.ok(isRsBasis(b) && b in BASIS_LABELS, `缺 ReelShort 依据 ${b}`);

  const on = pickQuery(base, {
    youtubeOk: true,
    datedOnly: true,
    inUseOnly: true,
    wide: true,
    withOff: true,
  });
  assert.equal(on, "?off=1&w=1&yt=1&dated=1&inuse=1");
  const back = parsePickRequest(new URLSearchParams(on));
  assert.equal(back.youtubeOk, true);
  assert.equal(back.datedOnly, true);
  assert.equal(back.inUseOnly, true);
  assert.equal(back.wide, true);
  assert.equal(back.withOff, true);
});

test("ReelShort 行键：前缀 + book_id，反解只认 book_id 形状", () => {
  assert.equal(
    reelshortRowKey("6a66ccd4cbbafb058e0a7a4f"),
    "reelshort-6a66ccd4cbbafb058e0a7a4f",
  );
  assert.equal(
    reelshortId("reelshort-6a66ccd4cbbafb058e0a7a4f"),
    "6a66ccd4cbbafb058e0a7a4f",
  );
  assert.equal(
    reelshortId("kalos-the-dragon-s-contract-x"),
    "",
    "剧场行键不是 ReelShort 行",
  );
  assert.equal(reelshortId("reelshort-"), "", "空 id 不是");
  assert.equal(
    reelshortId("reelshort-../x"),
    "",
    "只认字母数字，拼不出别的查询",
  );
  assert.equal(reelshortId("reelshort-" + "a".repeat(41)), "", "超长拒绝");
});
