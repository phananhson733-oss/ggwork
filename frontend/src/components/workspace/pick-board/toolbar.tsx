// PORTED_FROM: realshort@816ca2e src/components/admin/pick/toolbar.tsx
// 本地改动：pickHref 的前缀改成 /workspace/pick-data；Tabs 加「同步与导入」（imports），tab 链接清掉回放的 result（C27）；
// 在用剧场与依据标签改读版本规则（rules.inUse / rules.basisLabels），原来模块级的「其他剧场」挪进 PlatformChips 现算；
// 搜索表单加隐藏的 v；所有 Link 加 prefetch={false}；Filters 拆成几个小组件（函数 <50 行），文案与链接逐条不变。
import Link from "next/link";
import type { ReactNode } from "react";

import { pageSequence } from "@/core/pick-board/pager";
import {
  BASES,
  PAGE_SIZES,
  PLATFORMS,
  PLATFORM_LABELS,
  POSTED_FILTERS,
  POSTED_LABELS,
  SORTS,
  pickQuery,
  type ListTab,
  type PickRequest,
  type Platform,
  type Sort,
} from "@/core/pick-board/request";
import type { BoardRules } from "@/core/pick-board/rules";
import type { PickFacets } from "@/server/pick-board";

/**
 * Tab 与筛选，全部走 URL 参数（筛选结果能发给同事、能加书签、刷新不丢）。
 * 站内跳转一律 next/link 的 Link（不预取：每个链接都是一次镜像库查询）；换筛选条件时回到第 1 页。
 * 版面照 artifact（xuanju.tmpl.html 的 .tabs / .chips / .chip / .pg）：tab 带计数、chips 标签 26px 起、
 * 「其他剧场」是一颗虚线 chip；选中态 bg-brand + text-on-brand（观测台实测过对比度，不用两个粉色叠）。
 */
export const BOARD_PATH = "/workspace/pick-data";

export function pickHref(
  req: PickRequest,
  patch: Partial<PickRequest>,
): string {
  const next = { ...req, page: patch.page ?? 1, ...patch };
  return `${BOARD_PATH}${pickQuery(next)}`;
}

/** 证据页是从哪个列表 tab 进来的：在证据页里再点同名行沿用原来源，别的 tab 就是自己；规则与同步 tab 没有行，回选剧 */
export function originTab(req: PickRequest): ListTab {
  if (req.tab === "row") return req.from;
  if (req.tab === "rules" || req.tab === "imports") return "pick";
  return req.tab;
}

/**
 * 证据页链接。来源 tab 编进 URL（`from=`），「返回列表」与顶部 tab 才回得到同一张表——
 * 没有它，从全部剧库第 7 页点进来再返回，落到的是选剧 tab 的第 7 页（/qa 2026-09-11 ISSUE-001）。
 */
export function rowHref(req: PickRequest, rowKey: string): string {
  return pickHref(req, {
    tab: "row",
    rowKey,
    page: req.page,
    from: originTab(req),
  });
}

export const TAB_LABELS = {
  pick: "选剧",
  all: "全部剧库",
  rank: "榜单",
  posted: "发布记录",
  rules: "剧场规则",
  imports: "同步与导入",
  row: "证据页",
} as const;
const SORT_LABELS = {
  evidence: "证据时间：最近在前",
  listed: "剧单日期",
  title: "剧名",
} as const;
/**
 * 三种排序的具体规则，挂在 chip 的 hover 上（用户 2026-09-12 要求写明）。
 * 【必须与 server/pick-board/queries.ts 的 orderBy() 逐键一致】——tests/unit/core/pick-board/sort-hints.test.ts 钉着两边的键序；
 * 这里写的是给人看的版本，那边是 SQL，漂了就是页面在撒谎。
 */
const SORT_HINTS: Record<Sort, string> = {
  evidence:
    "第一键：最近一条证据的日期，新的在前；没有日期的证据排在全部有日期的行之后。" +
    "证据日期取这一行所有信号里最近的一条，各信号自己的日期是：KalosTV 日榜 = 榜单日期、周热门 = 周起、FlickReels 爆款 = 入榜日期、" +
    "GoodShort 爆款 / 重点推荐 = 推荐日期、历史高充值 = 表内日期、鹊娱两张 Top25 = 榜单日期；ReelShort 行 = 最近过滤后出站日 / 最近账单日 / GSC 采集日三者里最近的。" +
    "ShortMax / MoboReels 评级、StarShort 高充值剧单、DramaBox 运营备注没有日期，不拿剧单日期代填，所以只有这几种信号的行排在最后。" +
    "同一天再按：剧单日期（新在前，没有的靠后）→ 剧名 → 剧场 → 行键。这只是浏览顺序，不是投放价值排序。",
  listed:
    "第一键：剧单日期，就是剧单里的推荐 / 上新日期（ReelShort 行用上线日期），新的在前；没有日期的排最后。同一天再按剧名 → 行键。",
  title: "剧名按字符序 A→Z（数据库的字符序，不是拼音）；同名再按剧场 → 行键。",
};

export interface TabCounts {
  pick?: number;
  all?: number;
  posted?: number;
}

/** 搜索词在剧单三个 tab 之间带着走（选剧里没搜到就去全部剧库），进出发布记录、规则与同步 tab 时清掉：那边搜的是另一张表 */
function tabQuery(req: PickRequest, dest: PickRequest["tab"]): string {
  const crossesPosted = (dest === "posted") !== (originTab(req) === "posted");
  return crossesPosted || dest === "rules" || dest === "imports" ? "" : req.q;
}

function TabLink({
  req,
  t,
  label,
  count,
}: {
  req: PickRequest;
  t: PickRequest["tab"];
  label: string;
  count?: number;
}) {
  const active = t === req.tab;
  /* 证据页 tab 指回它自己，来源不变；别的 tab 就是目的地本身 */
  const dest = t === "row" ? originTab(req) : t;
  return (
    <Link
      prefetch={false}
      /* 回放的 result 只属于选剧 tab 的那一屏：点任何 tab 都回到普通列表（C27） */
      href={pickHref(req, {
        tab: t,
        page: 1,
        rowKey: t === "row" ? req.rowKey : "",
        sd: "",
        q: tabQuery(req, dest),
        result: "",
      })}
      aria-current={active ? "page" : undefined}
      className={`-mb-px border-b-2 px-3.5 py-2.5 text-[14px] whitespace-nowrap ${
        active
          ? "border-brand text-ink-1 font-semibold"
          : "text-helper hover:text-ink-1 border-transparent"
      }`}
    >
      {label}
      {count !== undefined ? (
        <small className="text-ink-dim ml-1 text-[12px] font-normal tabular-nums">
          {count.toLocaleString("en-US")}
        </small>
      ) : null}
    </Link>
  );
}

export function Tabs({
  req,
  counts = {},
}: {
  req: PickRequest;
  counts?: TabCounts;
}) {
  const tabs: { t: PickRequest["tab"]; label: string; count?: number }[] = [
    { t: "pick", label: TAB_LABELS.pick, count: counts.pick },
    { t: "all", label: TAB_LABELS.all, count: counts.all },
    { t: "rank", label: TAB_LABELS.rank },
    { t: "posted", label: TAB_LABELS.posted, count: counts.posted },
    { t: "rules", label: TAB_LABELS.rules },
    { t: "imports", label: TAB_LABELS.imports },
  ];
  if (req.tab === "row") tabs.push({ t: "row", label: TAB_LABELS.row });
  return (
    <div className="border-line mb-4 flex flex-wrap gap-1 border-b">
      {tabs.map(({ t, label, count }) => (
        <TabLink key={t} req={req} t={t} label={label} count={count} />
      ))}
    </div>
  );
}

export interface ChipItem {
  value: string;
  text: string;
  /** hover 里的规则说明 */
  title?: string;
  count?: number;
  muted?: boolean;
  /** artifact 的 .chip.more：虚线，用作「其他剧场 / 收起」这类开关 */
  dashed?: boolean;
}

export function Chip({
  href,
  on,
  item,
}: {
  href: string;
  on: boolean;
  item: ChipItem;
}) {
  return (
    <Link
      prefetch={false}
      href={href}
      aria-current={on ? "true" : undefined}
      title={item.title}
      className={`rounded-full border px-2.5 py-1 text-[12px] whitespace-nowrap ${
        on
          ? "border-brand bg-brand text-on-brand font-semibold"
          : `${item.dashed ? "border-dashed" : ""} border-line ${item.muted ? "text-ink-dim" : "text-ink-2"} hover:border-brand/50`
      }`}
    >
      {item.text}
      {item.count !== undefined ? (
        <small className="ml-[3px] text-[11px] tabular-nums opacity-75">
          {item.count.toLocaleString("en-US")}
        </small>
      ) : null}
    </Link>
  );
}

export function ChipLabel({ children }: { children: ReactNode }) {
  return (
    <span className="text-helper mr-0.5 min-w-[26px] shrink-0 text-[11px] tracking-[.04em]">
      {children}
    </span>
  );
}

export function Chips({
  label,
  items,
  current,
  build,
  trailing,
}: {
  label: string;
  items: readonly ChipItem[];
  current: string;
  build: (value: string) => string;
  trailing?: ReactNode;
}) {
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <ChipLabel>{label}</ChipLabel>
      {items.map((it) => (
        <Chip
          key={it.value}
          href={build(it.value)}
          on={it.value === current}
          item={it}
        />
      ))}
      {trailing}
    </div>
  );
}

function Toggle({
  on,
  href,
  text,
  title,
}: {
  on: boolean;
  href: string;
  text: string;
  title?: string;
}) {
  return (
    <Link
      prefetch={false}
      href={href}
      aria-pressed={on}
      title={title}
      className={`rounded-full border px-2.5 py-1 text-[12px] whitespace-nowrap ${
        on
          ? "border-brand bg-brand text-on-brand font-semibold"
          : "border-line text-ink-2 hover:border-brand/50"
      }`}
    >
      {text}
    </Link>
  );
}

function isPlatform(value: string): value is Platform {
  return (PLATFORMS as readonly string[]).includes(value);
}

/** 版本规则里的在用剧场（按版本里的顺序，只留本页认识的）与其余剧场 */
function platformGroups(rules: BoardRules): {
  inUse: readonly Platform[];
  others: readonly Platform[];
} {
  const inUse = rules.inUse.filter(isPlatform);
  return { inUse, others: PLATFORMS.filter((p) => !inUse.includes(p)) };
}

/** 收起 / 展开其他剧场的那颗虚线 chip */
function othersToggle(
  collapsed: boolean,
  others: number,
  rows: number,
): ChipItem {
  return collapsed
    ? {
        value: "__more",
        text: `其他剧场 ${others} 个`,
        count: rows,
        dashed: true,
        muted: true,
      }
    : { value: "__less", text: "收起其他剧场", dashed: true, muted: true };
}

/** 剧场 chips：在用的在前，其他的收在一颗虚线 chip 后面（`inuse=1` = 收起；默认展开，2026-09-10 用户要求） */
function PlatformChips({
  req,
  facets,
  rules,
}: {
  req: PickRequest;
  facets: PickFacets;
  rules: BoardRules;
}) {
  const { inUse, others } = platformGroups(rules);
  const count = (p: Platform) => facets.platforms[p] ?? 0;
  const othersN = others.reduce((a, p) => a + count(p), 0);
  const pickedOther = isPlatform(req.platform) && others.includes(req.platform);
  const collapsed = req.inUseOnly && !pickedOther;
  const chip = (p: Platform, muted = false): ChipItem => ({
    value: p,
    text: PLATFORM_LABELS[p],
    count: count(p),
    muted,
  });
  const items: ChipItem[] = [
    { value: "", text: "全部" },
    ...inUse.map((p) => chip(p)),
    ...(collapsed ? [] : others.map((p) => chip(p, true))),
  ];
  const toggle = othersToggle(collapsed, others.length, othersN);
  return (
    <Chips
      label="剧场"
      current={req.platform}
      items={items}
      build={(v) => pickHref(req, { platform: v as PickRequest["platform"] })}
      trailing={
        <Chip
          href={pickHref(req, {
            inUseOnly: !collapsed,
            /* 收起时若选中的是其他剧场，一并清掉，否则表里只剩一个看不见的条件 */
            platform: !collapsed && pickedOther ? "" : req.platform,
          })}
          on={false}
          item={toggle}
        />
      }
    />
  );
}

function Hidden({ name, value }: { name: string; value: string | number }) {
  return <input type="hidden" name={name} value={value} />;
}

/** 搜索框是一个 GET 表单：当前的筛选逐个用隐藏字段带着，版本 v 也带着（深链钉住同一份数据） */
function SearchForm({ req }: { req: PickRequest }) {
  return (
    <form
      action={BOARD_PATH}
      method="get"
      className="flex flex-wrap gap-2"
      role="search"
    >
      {req.tab !== "pick" ? <Hidden name="tab" value={req.tab} /> : null}
      {req.sort !== "evidence" ? <Hidden name="sort" value={req.sort} /> : null}
      {req.platform ? <Hidden name="platform" value={req.platform} /> : null}
      {req.lang ? <Hidden name="lang" value={req.lang} /> : null}
      {req.basis ? <Hidden name="basis" value={req.basis} /> : null}
      {req.posted ? <Hidden name="posted" value={req.posted} /> : null}
      {req.withOff ? <Hidden name="off" value="1" /> : null}
      {req.signalOnly ? <Hidden name="sig" value="1" /> : null}
      {req.wide ? <Hidden name="w" value="1" /> : null}
      {req.youtubeOk ? <Hidden name="yt" value="1" /> : null}
      {req.datedOnly ? <Hidden name="dated" value="1" /> : null}
      {req.inUseOnly ? <Hidden name="inuse" value="1" /> : null}
      {req.v !== null ? <Hidden name="v" value={req.v} /> : null}
      <label htmlFor="pick-search" className="sr-only">
        搜索剧名、中文名、行键或 book_id
      </label>
      <input
        id="pick-search"
        name="q"
        defaultValue={req.q}
        placeholder="搜索剧名 / 中文名 / 行键 / book_id"
        className="border-line bg-panel placeholder:text-ink-dim min-w-0 flex-1 rounded-[8px] border px-3 py-2 text-[14px]"
      />
      <button
        className="border-line hover:border-brand/50 rounded-[8px] border px-4 py-2 text-[14px]"
        type="submit"
      >
        搜索
      </button>
    </form>
  );
}

function LangBasisChips({
  req,
  facets,
  rules,
}: {
  req: PickRequest;
  facets: PickFacets;
  rules: BoardRules;
}) {
  return (
    <>
      <Chips
        label="语种"
        current={req.lang}
        items={[
          { value: "", text: "全部" },
          ...facets.langs.slice(0, 14).map((l) => ({
            value: l.lang,
            text: l.lang || "未标语种",
            count: l.n,
          })),
        ]}
        build={(v) => pickHref(req, { lang: v })}
      />
      <Chips
        label="依据"
        current={req.basis}
        items={[
          { value: "", text: req.tab === "pick" ? "任一信号" : "不限" },
          ...BASES.filter(
            (b) => (facets.bases[b] ?? 0) > 0 || b === req.basis,
          ).map((b) => ({
            value: b,
            text: rules.basisLabels[b],
            count: facets.bases[b] ?? 0,
          })),
        ]}
        build={(v) => pickHref(req, { basis: v as PickRequest["basis"] })}
      />
    </>
  );
}

function PostedSortChips({
  req,
  facets,
}: {
  req: PickRequest;
  facets: PickFacets;
}) {
  return (
    <>
      <Chips
        label="发布记录"
        current={req.posted}
        items={POSTED_FILTERS.map((p) => ({
          value: p,
          text: POSTED_LABELS[p],
          count: p ? facets.posted[p] : undefined,
        }))}
        build={(v) => pickHref(req, { posted: v as PickRequest["posted"] })}
      />
      <Chips
        label="排序"
        current={req.sort}
        items={SORTS.map((s) => ({
          value: s,
          text: SORT_LABELS[s],
          title: SORT_HINTS[s],
        }))}
        build={(v) => pickHref(req, { sort: v as PickRequest["sort"] })}
      />
    </>
  );
}

function ConditionToggles({ req }: { req: PickRequest }) {
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <ChipLabel>条件</ChipLabel>
      <Toggle
        on={req.youtubeOk}
        href={pickHref(req, { youtubeOk: !req.youtubeOk })}
        text="YouTube 可发"
        title="去掉禁 YouTube 的剧场；限剧单的剧场只留在 YouTube 剧单上的行"
      />
      <Toggle
        on={req.datedOnly}
        href={pickHref(req, { datedOnly: !req.datedOnly })}
        text="只看有日期的证据"
      />
      <Toggle
        on={req.withOff}
        href={pickHref(req, { withOff: !req.withOff })}
        text="含已下架"
      />
      {req.tab === "pick" ? (
        <Toggle
          on={req.wide}
          href={pickHref(req, { wide: !req.wide })}
          text="含仅剧单收录的行"
          title="没有任何榜单信号、只是在剧单上的行也进表，按剧单日期排"
        />
      ) : (
        <Toggle
          on={req.signalOnly}
          href={pickHref(req, { signalOnly: !req.signalOnly })}
          text="只看有信号的行"
        />
      )}
    </div>
  );
}

export function Filters({
  req,
  facets,
  rules,
}: {
  req: PickRequest;
  facets: PickFacets;
  rules: BoardRules;
}) {
  return (
    <div className="mb-3.5 flex flex-col gap-2.5">
      <SearchForm req={req} />
      <PlatformChips req={req} facets={facets} rules={rules} />
      <LangBasisChips req={req} facets={facets} rules={rules} />
      <PostedSortChips req={req} facets={facets} />
      <ConditionToggles req={req} />
    </div>
  );
}

/**
 * 页码越界（导入后行数缩水让旧书签落到最后一页之外，或手改 URL）。别的空状态给的补救
 * （含已下架 / 清掉搜索词）都不是这个空的原因，而 pager 在没有行时不渲染，人就没有回去的入口
 * （/qa 2026-09-11 ISSUE-003）。total 已经查出来了，不多一条查询。
 */
export function OutOfRange({
  req,
  total,
}: {
  req: PickRequest;
  total: number;
}) {
  const pages = Math.max(1, Math.ceil(total / req.size));
  return (
    <p className="border-line bg-panel text-helper rounded-[10px] border px-4 py-8 text-center text-[13px]">
      第 {req.page} 页不存在：当前条件下共 {total.toLocaleString("en-US")} 条、
      {pages.toLocaleString("en-US")} 页。
      <Link
        prefetch={false}
        href={pickHref(req, { page: 1 })}
        className="text-brand ml-2 hover:underline"
      >
        回第 1 页
      </Link>
      {pages > 1 ? (
        <Link
          prefetch={false}
          href={pickHref(req, { page: pages })}
          className="text-brand ml-3 hover:underline"
        >
          到最后一页（第 {pages.toLocaleString("en-US")} 页）
        </Link>
      ) : null}
    </p>
  );
}

/** artifact 的 .empty */
export function Empty({ children }: { children: ReactNode }) {
  return (
    <div className="border-line bg-panel text-helper rounded-[10px] border px-4 py-8 text-center text-[13px]">
      {children}
    </div>
  );
}

const PAGE_SPAN = 4;
const PAGER_LINK =
  "rounded-[8px] border border-line px-3 py-1.5 text-[13px] text-ink-2 hover:border-brand/50";

function PageSizes({ req }: { req: PickRequest }) {
  return (
    <div className="flex gap-1">
      {PAGE_SIZES.map((n) => (
        <Link
          prefetch={false}
          key={n}
          href={pickHref(req, { size: n })}
          aria-current={n === req.size ? "true" : undefined}
          className={
            n === req.size
              ? "border-brand bg-brand text-on-brand rounded-[8px] border px-2.5 py-1 text-[12px] font-semibold tabular-nums"
              : "border-line text-ink-2 hover:border-brand/50 rounded-[8px] border px-2.5 py-1 text-[12px] tabular-nums"
          }
        >
          {n}
        </Link>
      ))}
    </div>
  );
}

function PageNumbers({ req, pages }: { req: PickRequest; pages: number }) {
  return (
    <>
      {pageSequence(req.page, pages, PAGE_SPAN).map((n, i) =>
        n === null ? (
          <span
            key={`gap-${i}`}
            aria-hidden="true"
            className="text-ink-dim px-1 text-[13px]"
          >
            …
          </span>
        ) : n === req.page ? (
          <span
            key={n}
            aria-current="page"
            className="border-brand bg-brand text-on-brand min-w-9 rounded-[8px] border px-2.5 py-1.5 text-center text-[13px] font-semibold tabular-nums"
          >
            {n}
          </span>
        ) : (
          <Link
            prefetch={false}
            key={n}
            href={pickHref(req, { page: n })}
            className="border-line text-ink-2 hover:border-brand/50 min-w-9 rounded-[8px] border px-2.5 py-1.5 text-center text-[13px] tabular-nums"
          >
            {n}
          </Link>
        ),
      )}
    </>
  );
}

/** 页码与行数说明 + 每页行数切换 */
function PagerSummary({
  req,
  pages,
  count,
  total,
}: {
  req: PickRequest;
  pages: number | null;
  count: number;
  total: number | null;
}) {
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
      <span className="text-ink-dim text-[12px]">
        第 {req.page}
        {pages === null ? "" : ` / ${pages.toLocaleString("en-US")}`} 页 · 本页{" "}
        {count} 行
        {total === null ? "" : ` · 共 ${total.toLocaleString("en-US")} 条`}
      </span>
      <div className="flex-1" />
      <span className="text-ink-dim text-[12px]">每页</span>
      <PageSizes req={req} />
    </div>
  );
}

export function Pager({
  req,
  hasMore,
  count,
  total,
}: {
  req: PickRequest;
  hasMore: boolean;
  count: number;
  /** null = 这一屏不分页（涨幅榜固定取前 50），只显示行数 */
  total: number | null;
}) {
  const pages =
    total === null ? null : Math.max(1, Math.ceil(total / req.size));
  return (
    <div className="border-line mt-3 flex flex-col gap-3 border-t pt-3">
      <PagerSummary req={req} pages={pages} count={count} total={total} />
      <div className="flex flex-wrap items-center gap-1.5">
        {req.page > 1 ? (
          <Link
            prefetch={false}
            href={pickHref(req, { page: req.page - 1 })}
            className={PAGER_LINK}
          >
            上一页
          </Link>
        ) : null}
        {pages === null ? null : <PageNumbers req={req} pages={pages} />}
        {hasMore ? (
          <Link
            prefetch={false}
            href={pickHref(req, { page: req.page + 1 })}
            className={PAGER_LINK}
          >
            下一页
          </Link>
        ) : null}
      </div>
    </div>
  );
}
