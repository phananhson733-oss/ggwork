/**
 * The radar's two tabs and an identity's detail (plan TR-24; design 2.2, 5.6,
 * 5.8, 6.1; premises 1 and 2). b_only: the trends tab says per-drama trends
 * are not live and lists the discovery queue and the uncovered units, with no
 * curve. Every section has an explicit empty state, and no text node calls an
 * unobserved count zero. Actionability is computed at the `now` passed in.
 */
import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, render, screen } from "@testing-library/react";
import type { ReactNode } from "react";

import { ObsView } from "@/components/workspace/pick-board/views/obs-view";
import { forbiddenIn } from "@/core/pick/obs-format";
import { TRENDS_B_ONLY } from "@/core/pick/obs-wording";
import { parsePickRequest } from "@/core/pick-board/request";
import type { ObsTabData } from "@/server/pick-board";

import {
  channelLoad,
  detailData,
  emptyChannel,
  G_LIVE,
  gscSet,
  IDENTITY,
  link,
  OBS_NOW,
  run,
  searchData,
  state,
  summary,
  T_LIVE,
  T_SHADOW,
  trendsData,
  trendsSet,
} from "../obs-fixtures";

rs.mock("next/link", () => ({
  default: ({
    href,
    prefetch,
    children,
    ...rest
  }: {
    href: string;
    prefetch?: boolean;
    children: ReactNode;
  }) => (
    <a href={href} data-prefetch={String(prefetch)} {...rest}>
      {children}
    </a>
  ),
}));

afterEach(cleanup);

function show(
  data: ObsTabData,
  params: Record<string, string> = {},
  now = OBS_NOW,
) {
  const tab = data.kind === "detail" ? data.tab : data.kind;
  const req = parsePickRequest({ tab, ...params });
  return render(<ObsView data={data} req={req} now={now} />).container;
}

/** Every text node on its own: a phrase that calls an unobserved count zero (premise 1). */
function forbiddenTexts(root: Element): string[] {
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  const found: string[] = [];
  for (let node = walker.nextNode(); node; node = walker.nextNode())
    found.push(...forbiddenIn(node.textContent ?? ""));
  return found;
}

const section = (root: Element, name: string) =>
  root.querySelector(`[data-obs-section="${name}"]`);

describe("the trends tab (b_only)", () => {
  it("says per-drama trends are not live, lists the queue and the uncovered units, draws no curve", () => {
    const root = show(trendsData());
    expect(screen.getByText(TRENDS_B_ONLY)).toBeTruthy();
    expect(root.querySelector("svg")).toBeNull();
    const routes = Array.from(root.querySelectorAll("[data-obs-route]")).map(
      (node) => node.getAttribute("data-obs-route"),
    );
    expect(routes).toEqual(["queue", "display_only"]);
    expect(section(root, "uncovered")?.textContent).toContain(
      "未覆盖 1 个单元",
    );
    expect(section(root, "summary")?.textContent).toContain(
      "歧义，不判定（设计如此）：9 行",
    );
    expect(screen.getByText("Data source: Google Trends")).toBeTruthy();
    expect(forbiddenTexts(root)).toEqual([]);
  });

  it("a matched term and an uncovered unit link to the identity's detail, pinned set kept", () => {
    const root = show(trendsData(), { obs: T_LIVE });
    const details = Array.from(root.querySelectorAll("a"))
      .map((a) => a.getAttribute("href") ?? "")
      .filter((href) => href.includes("oid="));
    expect(details.length).toBeGreaterThanOrEqual(2);
    for (const href of details) {
      expect(href).toContain(`obs=${T_LIVE}`);
      expect(href).toContain(`oid=${encodeURIComponent(IDENTITY)}`);
    }
  });

  it("with no set: the channel says so, the note stays, no counts", () => {
    const root = show(
      trendsData({ trends: emptyChannel("trends"), discoveries: null }),
    );
    expect(screen.getByText(TRENDS_B_ONLY)).toBeTruthy();
    expect(root.querySelector('[data-obs-empty="set"]')?.textContent).toContain(
      "还没有发布过观测集合",
    );
    expect(screen.getByText("还没有运行记录。")).toBeTruthy();
    expect(screen.getByText("这个通道现在没有需要提醒的状态。")).toBeTruthy();
    expect(section(root, "summary")).toBeNull();
    expect(section(root, "discoveries")).toBeNull();
  });

  it("an empty queue and no out-of-pool hit are sentences; no A tier section in b_only", () => {
    const root = show(
      trendsData({ discoveries: { rows: [], counts: {}, truncated: false } }),
    );
    expect(screen.getByText("这个集合的发现段没有进队列的词。")).toBeTruthy();
    expect(screen.getByText("这个集合没有池外命中。")).toBeTruthy();
    expect(root.querySelector('[data-obs-route="a_tier"]')).toBeNull();
  });

  it("no uncovered unit is a sentence", () => {
    const set = trendsSet({ summary: summary("trends_no_zero_rate") });
    const root = show(
      trendsData({ trends: channelLoad("trends", { shown: set }) }),
    );
    expect(section(root, "uncovered")?.textContent).toContain(
      "这个集合没有未覆盖的单元。",
    );
    expect(section(root, "summary")?.textContent).toContain("全零率 未计算");
  });

  it("a shadow set is marked; with no live set the page says it shows the newest shadow one", () => {
    const shadow = trendsSet({ set_id: T_SHADOW, mode: "shadow" });
    const root = show(
      trendsData({
        trends: channelLoad("trends", {
          shown: shadow,
          live: null,
          recent: [
            {
              set_id: shadow.set_id,
              mode: shadow.mode,
              published_at: shadow.published_at,
            },
          ],
        }),
      }),
    );
    expect(root.querySelector('[data-obs-shadow="true"]')?.textContent).toBe(
      "影子",
    );
    expect(root.textContent).toContain(
      "还没有生效的集合，下面是最新发布的影子集合。",
    );
    expect(root.textContent).toContain("影子集合只在资料页展示，不进智能体。");
  });

  it("a pin that fell back says so; a pinned old set links back to the current one", () => {
    const missing = show(
      trendsData({ trends: channelLoad("trends", { pin: "missing" }) }),
    );
    expect(missing.textContent).toContain(
      "链接钉住的集合不是这个通道已发布的集合",
    );
    cleanup();
    const pinned = trendsSet({ set_id: T_SHADOW, mode: "shadow" });
    const root = show(
      trendsData({
        trends: channelLoad("trends", { shown: pinned, pin: "shown" }),
      }),
      { obs: T_SHADOW },
    );
    const back = screen.getByText("回到当前集合");
    expect(back.getAttribute("href")).toBe("/workspace/pick-data?tab=trends");
    expect(root.textContent).toContain("这是钉住的集合，不是当前生效的集合。");
  });

  it("the latest run's codes are banners at now: a red one is an alert", () => {
    const load = channelLoad("trends", {
      latestRun: run("trends", {
        status_codes: ["extinguished_today"],
        mode: "live",
      }),
    });
    show(trendsData({ trends: load }));
    expect(screen.getByRole("alert").textContent).toContain("Google Trends");
  });
});

describe("the search tab", () => {
  it("keeps the three coverage layers, the windows and the V checks with what was reused", () => {
    const root = show(searchData());
    const coverage = section(root, "coverage")?.textContent ?? "";
    expect(coverage).toContain("第一层：已收明细守恒");
    expect(coverage).toContain("第二层：明细缺口");
    expect(coverage).toContain("第三层（不可知部分）");
    expect(coverage).toContain("全站总量 91500，缺口 3500");
    expect(section(root, "windows")?.textContent).toContain(
      "24 小时窗口：完整",
    );
    expect(section(root, "vchecks")?.textContent).toMatch(
      /按 D26 沿用的 Vd \d+ 项/,
    );
    expect(forbiddenTexts(root)).toEqual([]);
  });

  it("groups the rows by the frozen market map, Bulgaria on its own", () => {
    const root = show(searchData());
    const groups = Array.from(root.querySelectorAll("[data-obs-group]")).map(
      (node) => node.getAttribute("data-obs-group"),
    );
    expect(groups).toEqual(["north_america", "bulgaria"]);
    expect(
      root.querySelector('[data-obs-group="bulgaria"]')?.textContent,
    ).toContain("保语页面 98.1% 的点击来自一部剧");
  });

  it("formal and descriptive labels apart, each with its condition and raw counts; unobserved is never zero", () => {
    const root = show(searchData());
    const formal = root.querySelector(
      '[data-obs-label="surge"][data-obs-formal="true"]',
    );
    expect(formal?.textContent).toContain("W0 325 · W−1 82");
    const descriptive = root.querySelector(
      '[data-obs-label="surge"][data-obs-formal="false"]',
    );
    expect(descriptive?.textContent).toContain("W−1 未观测到");
    expect(descriptive?.textContent).toContain("小基数曝光上升");
    expect(root.textContent).toContain("两份下界一致（准入）");
  });

  it("the paste row is plain text, selected whole", () => {
    const root = show(searchData());
    const pre = root.querySelector("[data-obs-paste-row] pre");
    expect(pre?.textContent?.split("\t")).toHaveLength(7);
    expect(pre?.className).toContain("select-all");
  });

  it("24-hour rows are provisional when the set had no formal 24-hour window", () => {
    const set = gscSet({ summary: summary("gsc_window_gap_no_formal_24h") });
    const root = show(searchData({ gsc: channelLoad("gsc", { shown: set }) }));
    expect(section(root, "windows")?.textContent).toContain(
      "24 小时窗口：暂定",
    );
    expect(root.querySelector("[data-obs-state]")?.textContent).toContain(
      "（暂定）",
    );
  });

  it("no site total says the gap cannot be computed, not zero", () => {
    const set = gscSet({ summary: summary("gsc_no_site_total") });
    const root = show(searchData({ gsc: channelLoad("gsc", { shown: set }) }));
    expect(section(root, "coverage")?.textContent).toContain(
      "两种全站总量都没拿到，缺口算不出",
    );
  });

  it("empty states: no set, no rows, rows without labels", () => {
    const none = show(searchData({ gsc: emptyChannel("gsc"), states: null }));
    expect(none.textContent).toContain("这个通道还没有发布过观测集合");
    expect(section(none, "coverage")).toBeNull();
    cleanup();
    const empty = show(
      searchData({
        states: { rows: [], total: 0, labeled: 0, truncated: false },
      }),
    );
    expect(section(empty, "states")?.textContent).toContain(
      "这个集合没有判定行。",
    );
    cleanup();
    const quiet = show(
      searchData({
        states: { rows: [], total: 4, labeled: 0, truncated: false },
      }),
    );
    expect(section(quiet, "states")?.textContent).toContain(
      "这个集合的 4 行判定都没有命中任何标签。",
    );
  });
});

describe("an identity's detail", () => {
  it("both channels side by side, then the link facts of the pair", () => {
    const root = show(detailData());
    const columns = Array.from(root.querySelectorAll("[data-obs-column]")).map(
      (node) => node.getAttribute("data-obs-column"),
    );
    expect(columns).toEqual(["trends", "gsc"]);
    expect(
      screen.getByRole("heading", { level: 2, name: "The Alpha's Bride" }),
    ).toBeTruthy();
    expect(
      screen.getByText("返回「搜索表现（GSC）」").getAttribute("href"),
    ).toBe("/workspace/pick-data?tab=search");
    expect(forbiddenTexts(root)).toEqual([]);
  });

  it("a timely both-rising fact is actionable now, and untimely once the GSC set is over 6 hours old", () => {
    const root = show(detailData());
    const fact = root.querySelector('[data-obs-link="both_rising"]');
    expect(fact?.textContent).toContain("双涨");
    expect(fact?.textContent).toContain("可行动：进发布清单");
    cleanup();
    const late = show(detailData(), {}, new Date("2026-09-25T08:00:00Z"));
    expect(
      late.querySelector('[data-obs-link="both_rising"]')?.textContent,
    ).toContain("时效不符：GSC 集合发布已超过 6 小时");
  });

  it("global parallel and different markets are shown, never acted on", () => {
    const facts = [
      link({
        id: 80,
        label: "global_parallel",
        country: "ALL",
        trends_geo: "WW",
      }),
      link({
        id: 81,
        label: "different_markets",
        country: null,
        trends_geo: null,
        trends_row_id: null,
        gsc_row_id: null,
      }),
    ];
    const root = show(detailData({ links: facts }));
    const global = root.querySelector('[data-obs-link="global_parallel"]');
    expect(global?.textContent).toContain("全球同向");
    expect(global?.textContent).toContain("只作展示，不带动作");
    const markets = root.querySelector('[data-obs-link="different_markets"]');
    expect(markets?.textContent).toContain("不同市场信号");
    expect(markets?.textContent).toContain("身份层（没有共同国家）");
  });

  it("an unregistered link-rules version is not judged", () => {
    const root = show(
      detailData({ links: [link({ link_rules_version: "link-rules-v9" })] }),
    );
    expect(root.textContent).toContain(
      "link-rules 版本 link-rules-v9 本页没有登记，不判定可行动性",
    );
  });

  it("empty states: no rows on either side, no fact, sets of different modes, a channel with no set", () => {
    const quiet = show(detailData({ states: [], discoveries: [], links: [] }));
    expect(quiet.textContent).toContain(
      "这个 Trends 集合里没有这部剧的判定行（逐剧趋势未上线）。",
    );
    expect(quiet.textContent).toContain("这个 GSC 集合里没有这部剧的判定行。");
    expect(quiet.textContent).toContain("这个集合的发现段没有对上这部剧的词。");
    expect(quiet.textContent).toContain("这一对集合里，这部剧没有联动事实。");
    cleanup();
    const shadow = gscSet({ mode: "shadow" });
    const modes = show(
      detailData({ gsc: channelLoad("gsc", { shown: shadow }), links: [] }),
    );
    expect(modes.textContent).toContain("联动只在同模式的集合之间写入");
    cleanup();
    const missing = show(
      detailData({ trends: emptyChannel("trends"), links: [] }),
    );
    expect(missing.textContent).toContain(
      "两个通道各有一个已发布集合时才有联动事实。",
    );
  });

  it("only the tab's channel offers sets to pin, and pinning keeps the identity", () => {
    const shadow = gscSet({
      set_id: "3f2e1d0c9b8a7f6e5d4c3b2a1f0e9d8d",
      mode: "shadow",
    });
    const recent = (load: ReturnType<typeof channelLoad>) => [
      ...load.recent,
      {
        set_id: shadow.set_id,
        mode: shadow.mode,
        published_at: shadow.published_at,
      },
    ];
    const gsc = channelLoad("gsc");
    const trends = channelLoad("trends");
    const root = show(
      detailData({
        gsc: { ...gsc, recent: recent(gsc) },
        trends: { ...trends, recent: [...trends.recent, trends.recent[0]!] },
      }),
      { oid: IDENTITY },
    );
    const pins = Array.from(root.querySelectorAll("a"))
      .map((a) => a.getAttribute("href") ?? "")
      .filter((href) => href.includes("obs="));
    expect(pins.length).toBeGreaterThan(0);
    for (const href of pins) {
      expect(href).toContain("tab=search");
      expect(href).toContain(`oid=${encodeURIComponent(IDENTITY)}`);
      expect(href).not.toContain(`obs=${T_LIVE}`);
    }
    expect(pins.some((href) => href.includes(`obs=${G_LIVE}`))).toBe(true);
  });

  it("a trends row reads its state, confirmation and correspondence", () => {
    const root = show(detailData({ states: [state("states_trends")] }));
    const row = root.querySelector(
      '[data-obs-column="trends"] [data-obs-state]',
    );
    expect(row?.textContent).toContain("上升观察");
    expect(row?.textContent).toContain("已确认（相邻两天都成立）");
    expect(row?.textContent).toContain("已人工确认对应");
  });
});

describe("codex review (P2, P3)", () => {
  const detailHrefs = (root: Element) =>
    Array.from(root.querySelectorAll("a"))
      .map((a) => a.getAttribute("href") ?? "")
      .filter((href) => href.includes("oid="));

  it("the title of a small-base descriptive surge is the small-base rise, never the surge", () => {
    const root = show(searchData());
    const title = root.querySelector(
      '[data-obs-label="surge"][data-obs-formal="false"] [data-obs-label-title]',
    );
    expect(title?.textContent).toBe("小基数曝光上升");
  });

  it("a list opened without a pin links each identity to the set it showed", () => {
    const trends = show(trendsData());
    expect(detailHrefs(trends).length).toBeGreaterThanOrEqual(2);
    for (const href of detailHrefs(trends))
      expect(href).toContain(`obs=${T_LIVE}`);
    cleanup();
    const search = show(searchData());
    expect(detailHrefs(search).length).toBeGreaterThan(0);
    for (const href of detailHrefs(search))
      expect(href).toContain(`obs=${G_LIVE}`);
  });

  it("V checks with no new request still show what was reused or not sent", () => {
    const vchecks = {
      requests: 0,
      succeeded: 0,
      failed: 0,
      truncated: 0,
      stale: 0,
      reused: 12,
      regex_overflow: 2,
    };
    const base = gscSet();
    const set = gscSet({
      summary: { ...base.summary, vcheck_summary: vchecks },
    });
    const root = show(searchData({ gsc: channelLoad("gsc", { shown: set }) }));
    const text = section(root, "vchecks")?.textContent ?? "";
    expect(text).toContain("按 D26 沿用的 Vd 12 项");
    expect(text).toContain("正则分块溢出 2");
    expect(text).not.toContain("没有逐剧核对");
    cleanup();
    const idle = gscSet({
      summary: {
        ...base.summary,
        vcheck_summary: { ...vchecks, reused: 0, regex_overflow: 0 },
      },
    });
    const quiet = show(
      searchData({ gsc: channelLoad("gsc", { shown: idle }) }),
    );
    expect(section(quiet, "vchecks")?.textContent).toContain(
      "这一轮没有逐剧核对：没有请求，也没有沿用。",
    );
  });

  it("a pinned older shadow set is not called the newest", () => {
    const older = trendsSet({ set_id: T_SHADOW, mode: "shadow" });
    const newest = trendsSet({
      set_id: "7a1c0e9b5d3f4a2e8b6c1d0f9e8a7b6e",
      mode: "shadow",
      published_at: "2026-09-25T03:00:00.000000+00:00",
    });
    const brief = (set: typeof older) => ({
      set_id: set.set_id,
      mode: set.mode,
      published_at: set.published_at,
    });
    const root = show(
      trendsData({
        trends: channelLoad("trends", {
          shown: older,
          live: null,
          pin: "shown",
          recent: [brief(newest), brief(older)],
        }),
      }),
      { obs: T_SHADOW },
    );
    expect(root.textContent).toContain(
      "还没有生效的集合，下面是钉住的影子集合。",
    );
    expect(root.textContent).not.toContain("最新发布的影子集合");
  });
});
