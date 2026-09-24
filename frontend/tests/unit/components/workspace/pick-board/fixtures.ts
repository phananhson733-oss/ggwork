/**
 * Synthetic data for the pick-board component tests. Every title, id and
 * link here is made up; the version rules come from the backend contract
 * fixture copy (tests/unit/core/pick-board/fixtures/rules.json), built the
 * way the page builds them, for version 7.
 */
import { parsePickRequest, type PickRequest } from "@/core/pick-board/request";
import { buildBoardRules, type BoardRules } from "@/core/pick-board/rules";
import type {
  BillRow,
  BillTotals,
  CatalogAccount,
  ObserveRow,
  PickRow,
  PickSignal,
  PostedLinks,
  PostedRecord,
  RankMeta,
  RankRow,
  ReelshortDetail,
  RowDetail,
} from "@/server/pick-board";

import rulesFixture from "../../../core/pick-board/fixtures/rules.json";

export const VERSION = 7;
export const AS_OF = new Date("2026-09-24T03:40:00Z");

type RawPlatformRule = (typeof rulesFixture.platformRules)["touchshort"];
/** The fixture shape; platformRules may gain theaters the page does not know. */
type RawRules = typeof rulesFixture & {
  platformRules: Record<string, RawPlatformRule>;
};

/** The fixture rules, optionally edited first (a deep copy, never the import). */
export function boardRules(edit?: (raw: RawRules) => void): BoardRules {
  const raw = structuredClone(rulesFixture);
  edit?.(raw);
  return buildBoardRules(raw, VERSION);
}

export function request(params: Record<string, string> = {}): PickRequest {
  return parsePickRequest({ v: String(VERSION), ...params });
}

export function signal(patch: Partial<PickSignal> = {}): PickSignal {
  return {
    kind: "kd",
    ord: 0,
    evidenceOn: "2026-09-20",
    rank: 3,
    grade: "",
    note: "",
    payload: {
      days: 4,
      best: 2,
      first: "2026-09-12",
      h: [
        ["2026-09-20", 3],
        ["2026-09-19", 2],
      ],
    },
    ...patch,
  };
}

export function pickRow(patch: Partial<PickRow> = {}): PickRow {
  return {
    rowKey: "kalos-demo-1",
    platform: "kalos",
    sourceTable: "KalosTV 剧单",
    title: "Demo Bride",
    titleCn: "示例新娘",
    lang: "英语",
    kind: "译制剧",
    origin: "海外网文IP",
    tags: "",
    listedOn: "2026-09-10",
    hasPan: true,
    episodes: 80,
    payStart: 10,
    youtube: false,
    mergedRows: 1,
    offOn: null,
    reoffNote: "",
    inSiteIds: [],
    legacyOnly: false,
    siteOther: false,
    hasSignal: true,
    latestEvidenceOn: "2026-09-20",
    signals: [signal()],
    posted: [],
    ...patch,
  };
}

export function observeRow(patch: Partial<ObserveRow> = {}): ObserveRow {
  return {
    id: "demo0001",
    title: "Demo Heir",
    locale: "en",
    slug: "demo-heir",
    publishAt: new Date("2026-08-01T00:00:00Z"),
    chapterCount: 60,
    payStart: 11,
    revenueCents: 123_456,
    promotersCnt: 40,
    searchImpressions: 12,
    searchDataAt: new Date("2026-09-22T00:00:00Z"),
    detailSyncedAt: new Date("2026-09-23T00:00:00Z"),
    tags: ["女性", "逆袭"],
    metricsValid: true,
    syncedAt: new Date("2026-09-23T06:00:00Z"),
    baseline1At: new Date("2026-09-22T06:00:00Z"),
    baseline7At: new Date("2026-09-16T06:00:00Z"),
    revenueCents1: 120_000,
    promotersCnt1: 38,
    revenueCents7: 100_000,
    promotersCnt7: 30,
    baseline15At: null,
    revenueCents15: null,
    promotersCnt15: null,
    billOrders: 3,
    description: "A made-up synopsis.",
    clicks7: 5,
    lastClickOn: "2026-09-22",
    lastBillOn: "2026-09-21",
    ...patch,
  };
}

/** A ReelShort row as the union query returns it: a PickRow with rs metrics. */
export function reelshortPickRow(patch: Partial<PickRow> = {}): PickRow {
  return pickRow({
    rowKey: "reelshort-demo0001",
    platform: "reelshort",
    sourceTable: "ReelShort 片库",
    title: "Demo Heir",
    titleCn: "",
    lang: "英语",
    kind: "",
    origin: "",
    hasPan: false,
    signals: [],
    rs: observeRow(),
    ...patch,
  });
}

export function rankRow(patch: Partial<RankRow> = {}): RankRow {
  const base = pickRow();
  return {
    ...base,
    signal: signal(),
    dayRank: 3,
    dayNote: "剧场备注",
    ...patch,
  };
}

const DAYS = Array.from({ length: 20 }, (_, i) =>
  new Date(Date.UTC(2026, 8, 24 - i)).toISOString().slice(0, 10),
);

export function rankMeta(patch: Partial<RankMeta> = {}): RankMeta {
  return {
    counts: { kd: 10, kw: 4, sm: 3, rs_rr: 100, rs_bill: 5, rs_ledger: 9 },
    growthCounts: { d1: 1, d7: 2, dp1: 0, dp7: 3 },
    days: DAYS,
    weeks: Array.from({ length: 10 }, (_, i) => ({
      week: `W${10 - i}`,
      start: new Date(Date.UTC(2026, 8, 21 - i * 7)).toISOString().slice(0, 10),
    })),
    grades: { SSS: 1, A: 2 },
    day: DAYS[0] ?? "",
    week: "2026-09-21",
    dayResolution: "latest",
    weekResolution: "latest",
    ...patch,
  };
}

export function postedRecord(patch: Partial<PostedRecord> = {}): PostedRecord {
  return {
    sd: "SD-000001",
    feishuRecord: "rec0001",
    title: "Demo Bride",
    lang: "英语",
    platform: "KalosTV",
    life: "已上线",
    scheduled: true,
    onlineOn: "2026-09-01",
    why: "剧情反转多",
    note: "",
    archived: false,
    postCount: 3,
    schedCount: 1,
    firstPostOn: "2026-09-02",
    lastPostOn: "2026-09-20",
    viewsTotal: 1200,
    viewsCount: 2,
    metricAt: "2026-09-21",
    sources: ["KalosTV"],
    cats: ["女频"],
    who: ["运营甲"],
    accounts: ["demo_account"],
    createdOn: "2026-08-30",
    updatedOn: "2026-09-21",
    rowKeys: ["kalos-demo-1"],
    dramaIds: ["demo0001"],
    posts: [
      {
        d: "2026-09-02",
        acct: "demo_account",
        url: "https://video.example.com/p/1",
      },
      { d: "2026-09-10", acct: "demo_account", url: "[网盘信息已移除]" },
      {
        d: "2026-09-20",
        acct: "demo_account",
        url: "http://video.example.com/p/3",
      },
    ],
    ...patch,
  };
}

export function postedLinks(): PostedLinks {
  return {
    rows: new Map([
      [
        "kalos-demo-1",
        {
          rowKey: "kalos-demo-1",
          platform: "kalos",
          lang: "英语",
          title: "Demo Bride",
          offOn: null,
        },
      ],
    ]),
    dramas: new Map([
      [
        "demo0001",
        { id: "demo0001", locale: "en", slug: "demo-heir", title: "Demo Heir" },
      ],
    ]),
  };
}

export function accounts(): CatalogAccount[] {
  const base: CatalogAccount = {
    id: "acc1",
    name: "Demo Channel",
    url: "https://social.example.com/@demo",
    grp: "A 组",
    form: "解说",
    niche: "女频",
    status: "在用",
    fans: 1000,
    asOf: "2026-09-20",
  };
  return [
    base,
    { ...base, id: "acc2", name: "Scrubbed Channel", url: "[网盘信息已移除]" },
    {
      ...base,
      id: "acc3",
      name: "Plain Channel",
      url: "http://social.example.com/x",
    },
  ];
}

export function rowDetail(patch: Partial<RowDetail> = {}): RowDetail {
  return {
    row: pickRow(),
    siteDramas: [
      {
        id: "demo0001",
        locale: "en",
        slug: "demo-heir",
        title: "Demo Heir",
        chapterCount: 60,
        payStart: 11,
      },
    ],
    postedRecords: [postedRecord()],
    sameTitle: [
      {
        rowKey: "shortmax-demo-2",
        platform: "shortmax",
        lang: "英语",
        title: "Demo Bride",
        titleCn: "",
        offOn: null,
      },
    ],
    sameTitleTruncated: false,
    columnFilled: { episodes: true, payStart: true },
    ...patch,
  };
}

export function billRow(patch: Partial<BillRow> = {}): BillRow {
  return {
    billDate: "2026-09-21",
    bookId: "demo0002",
    canonicalId: "demo0001",
    title: "Demo Heir",
    locale: "en",
    promotionType: "link_book",
    orderCnt: 2,
    sourceRows: 3,
    sameDayClicks: 4,
    ...patch,
  };
}

export function billTotals(patch: Partial<BillTotals> = {}): BillTotals {
  return {
    rows: 12,
    mergedRows: 8,
    orders: 20,
    mergedWithClicks: 3,
    rowsWithClicks: 5,
    ...patch,
  };
}

export function reelshortDetail(
  patch: Partial<ReelshortDetail> = {},
): ReelshortDetail {
  return {
    row: observeRow(),
    series: [
      { day: "2026-09-22", revenueRaw: 120_000, promoters: 38 },
      { day: "2026-09-23", revenueRaw: 123_456, promoters: 40 },
    ],
    bill: [
      billRow(),
      billRow({ billDate: "2026-09-20", promotionType: "code" }),
    ],
    billTruncated: false,
    clicks: [{ day: "2026-09-22", human: 3, bot: 1 }],
    sameTitle: [
      {
        rowKey: "kalos-demo-1",
        platform: "kalos",
        lang: "英语",
        title: "Demo Heir",
        titleCn: "",
        offOn: null,
      },
    ],
    sameTitleTruncated: false,
    postedRecords: [postedRecord()],
    ...patch,
  };
}
