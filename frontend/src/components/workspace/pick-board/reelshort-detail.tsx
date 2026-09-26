// PORTED_FROM: realshort@816ca2e src/components/admin/pick/reelshort-detail.tsx
// 本地改动：素材与结算改读版本规则 rules.platformRules.reelshort（缺失时写「规则未知」）；删掉「我方分成」一组金额格与
// 原 Usd 格，订单笔数挪进「本站信号」；明细表只留日期、推广类型、订单数、合并的原始行数（不显示推广标识与金额），
// 截断时说明；「现在」取版本的 as_of（now 改名 asOf）；公开页是 ReelShort 站的绝对地址，新标签打开（★38）；
// Link 加 prefetch={false}；可选的 seriesTrimmedBefore 在清理日落进曲线窗口时说明早于哪天的点已清理；明细行的 book_id
// 不是正典 id 时在推广类型旁注明（兄弟资源同日同类型的行才分得开）；整页拆成几个小组件（函数 <50 行）。
// GGWork 配色：曲线与出站条用图表色 --chart-1 / --chart-2，同名未核是中性标签，下架是 warning pill，卡片圆角 12。
import Link from "next/link";
import type { ReactNode } from "react";

import {
  SERIES_DAYS,
  ageInDays,
  bucketLabel,
  candidateReasons,
  episodeAvailability,
  formatInt,
  formatObservedAt,
  formatUsd,
  lifecycleBucket,
} from "@/core/pick-board/metrics";
import { PLATFORM_LABELS, type PickRequest } from "@/core/pick-board/request";
import type { BoardRules } from "@/core/pick-board/rules";
import { dramaPath } from "@/core/pick-board/site";
import type { BillRow, ObserveRow, ReelshortDetail } from "@/server/pick-board";

import { MetricsValidTag, Tag } from "./cells";
import { ExternalLink } from "./links";
import { PostedRecordCard } from "./posted-record";
import { Delta, TagChips } from "./reelshort-cells";
import { Spark } from "./reelshort-spark";
import { TAB_LABELS, pickHref, rowHref } from "./toolbar";

/**
 * ReelShort 行的证据页：头部（含上游标签与简介）+ 按口径分开的指标格 + 两条 90 天曲线 + 14 天出站 + 订单明细，
 * 下面接「同名剧场行 · 未核」小表与我们的发布记录。返回链接回来源 tab。
 *
 * 【指标格按口径分组，不混排在一行里】：平台指标（上游 recent_revenue，全平台）/ 推广人数（上游 promoters_cnt 累计 +
 * 快照差分）/ 本站信号。分组标题里把口径写死，格子里只留数。本页不显示我方分成金额，订单只列笔数。
 */
function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="border-line bg-panel rounded-lg border p-4">
      <h3 className="mb-2 font-semibold">{title}</h3>
      {children}
    </section>
  );
}

type Tile = { label: string; value: ReactNode; note: string };

/** 一组同口径的指标格：组标题写口径，格子只放数字与一句来源说明 */
function TileGroup({
  title,
  hint,
  tiles,
  cols,
}: {
  title: string;
  hint: string;
  tiles: Tile[];
  cols: string;
}) {
  return (
    <div>
      <div className="mb-2 flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <span className="text-sm font-semibold">{title}</span>
        <span className="text-helper text-xs">{hint}</span>
      </div>
      <div className={`grid grid-cols-2 gap-3 ${cols}`}>
        {tiles.map((t) => (
          <div key={t.label} className="bg-raised rounded-lg p-3">
            <div className="text-helper text-xs">{t.label}</div>
            <div className="my-1 text-xl font-bold break-words tabular-nums">
              {t.value}
            </div>
            <p className="text-helper text-xs leading-relaxed">{t.note}</p>
          </div>
        ))}
      </div>
    </div>
  );
}

function IdentityLine({ row, asOf }: { row: ObserveRow; asOf: Date }) {
  const age = ageInDays(row.publishAt, asOf);
  return (
    <div className="text-helper mt-1 flex flex-wrap gap-x-4 gap-y-1 text-sm">
      <span>
        <span className="bg-raised text-ink-2 rounded-sm px-1 py-px font-semibold">
          {PLATFORM_LABELS.reelshort}
        </span>{" "}
        · 本站 CPS 片库
      </span>
      <span>
        book_id <code className="text-xs">{row.id}</code>
      </span>
      <span>
        上线 {row.publishAt ? row.publishAt.toISOString().slice(0, 10) : "未知"}
        {age === null
          ? null
          : ` · ${age} 天 · ${bucketLabel(lifecycleBucket(age))}`}
      </span>
      <span>
        {formatInt(row.chapterCount)} 集 ·{" "}
        {episodeAvailability(row.chapterCount, row.payStart)}
      </span>
      <ExternalLink
        href={dramaPath(row.locale, row.slug)}
        className="text-link hover:underline"
      >
        公开页 ↗
      </ExternalLink>
    </div>
  );
}

function HeadFacts({
  detail,
  requestedId,
  asOf,
}: {
  detail: ReelshortDetail;
  requestedId: string;
  asOf: Date;
}) {
  const { row, sameTitle } = detail;
  const reasons = candidateReasons(row);
  return (
    <div className="min-w-0">
      <h2 className="text-lg font-bold">
        {row.title}{" "}
        <span className="text-helper ml-2 text-sm font-normal">
          {row.locale}
        </span>
      </h2>
      <IdentityLine row={row} asOf={asOf} />
      {requestedId !== row.id ? (
        <p className="text-helper mt-2 text-sm">
          已定位到同组同语种正典资源；查询的原始 ID：{requestedId}
        </p>
      ) : null}
      <p className="text-helper mt-3 text-sm">
        详情采集：{formatObservedAt(row.detailSyncedAt)}。
        {reasons.join(" · ") || "尚无已保存的搜索匹配、近 7 天出站或预估订单"}。
      </p>
      <div className="mt-2 flex flex-wrap items-center gap-2">
        <span className="text-helper text-xs">上游标签</span>
        {row.tags.length ? (
          <TagChips tags={row.tags} />
        ) : (
          <span className="text-ink-dim text-sm">未取得标签</span>
        )}
      </div>
      <div className="mt-2 flex flex-wrap gap-1">
        <MetricsValidTag valid={row.metricsValid} />
        {sameTitle.length ? (
          <Tag tone="neutral">同名未核 · 剧场行 {sameTitle.length}</Tag>
        ) : null}
      </div>
    </div>
  );
}

function platformTiles(row: ObserveRow): Tile[] {
  return [
    {
      label: "30 天原值",
      value:
        row.metricsValid === false ? "未取得" : formatUsd(row.revenueCents),
      note: "上游当前值，不写货币符号",
    },
    {
      label: "较昨日净变化",
      value: <Delta current={row.revenueCents} previous={row.revenueCents1} />,
      note: `对比快照 ${formatObservedAt(row.baseline1At)}`,
    },
    {
      label: "较 7 天前净变化",
      value: <Delta current={row.revenueCents} previous={row.revenueCents7} />,
      note: `对比快照 ${formatObservedAt(row.baseline7At)}`,
    },
    {
      label: "较 15 天前净变化",
      value: <Delta current={row.revenueCents} previous={row.revenueCents15} />,
      note: `对比快照 ${formatObservedAt(row.baseline15At)}；快照 09-10 起有效，09-25 起才有数`,
    },
    {
      label: "累计",
      value: <span className="text-ink-dim">上游不给</span>,
      note: "上游只下发 30 天滚动值，没有累计销售额",
    },
  ];
}

function promoterTiles(row: ObserveRow): Tile[] {
  return [
    {
      label: "累计推广人数",
      value:
        row.metricsValid === false ? "未取得" : formatInt(row.promotersCnt),
      note: "上游已保存值，可能延迟或修订",
    },
    {
      label: "昨日新增",
      value: <Delta current={row.promotersCnt} previous={row.promotersCnt1} />,
      note: `对比快照 ${formatObservedAt(row.baseline1At)}`,
    },
    {
      label: "近 7 日新增",
      value: <Delta current={row.promotersCnt} previous={row.promotersCnt7} />,
      note: `对比快照 ${formatObservedAt(row.baseline7At)}；09-17 起才有数`,
    },
    {
      label: "近 15 日新增",
      value: <Delta current={row.promotersCnt} previous={row.promotersCnt15} />,
      note: `对比快照 ${formatObservedAt(row.baseline15At)}；09-25 起才有数`,
    },
  ];
}

function siteTiles(row: ObserveRow): Tile[] {
  const eff =
    row.metricsValid !== false && row.promotersCnt > 0
      ? row.revenueCents / row.promotersCnt
      : null;
  return [
    {
      label: "指标 / 推广人数",
      value: eff === null ? "—" : formatUsd(eff),
      note: "原值比值，不是收益率或预测",
    },
    {
      label: "订单笔数",
      value: formatInt(row.billOrders),
      note: "上游账单里有订单的行合计；本页不显示金额",
    },
    {
      label: "近 7 天出站次数",
      value: formatInt(row.clicks7),
      note: "168 小时；排除已识别爬虫",
    },
    {
      label: "已保存搜索匹配",
      value:
        row.searchImpressions > 0
          ? formatInt(row.searchImpressions)
          : "未匹配 / 未取得",
      note: formatObservedAt(row.searchDataAt),
    },
  ];
}

function MetricGroups({ row }: { row: ObserveRow }) {
  return (
    <div className="mt-4 flex flex-col gap-4">
      <TileGroup
        title="平台指标"
        hint="上游 recent_revenue：全平台所有分销商的 30 天滚动销售额，上游原值、单位与范围待核验，不是我方收入。三个「净变化」是我们自己每天存快照算出来的，缺快照显示「—」不是 0"
        cols="md:grid-cols-3 xl:grid-cols-5"
        tiles={platformTiles(row)}
      />
      <TileGroup
        title="推广人数"
        hint="上游 promoters_cnt：累计推广过这部剧的人数，全平台口径。新增是快照差分，缺快照显示「—」"
        cols="md:grid-cols-4"
        tiles={promoterTiles(row)}
      />
      <TileGroup
        title="本站信号"
        hint="站内自己量到的：比值、订单笔数、出站、搜索"
        cols="md:grid-cols-4"
        tiles={siteTiles(row)}
      />
    </div>
  );
}

function ValidityNote({ row }: { row: ObserveRow }) {
  const validity =
    row.metricsValid === true
      ? "原始字段校验通过。"
      : row.metricsValid === false
        ? "原始字段未取得有效值，不作真实零值解释。"
        : "历史指标未保留原始字段校验记录。";
  return (
    <p className="text-helper mt-3 text-xs leading-relaxed">
      指标采集：{formatObservedAt(row.syncedAt)}。{validity} 对比昨日快照：
      {formatObservedAt(row.baseline1At)}；7 天前：
      {formatObservedAt(row.baseline7At)}；15 天前：
      {formatObservedAt(row.baseline15At)}
      。搜索为页面与查询维度映射后的需求信号，非原始逐剧统计；窗口与暂定状态见页底来源说明。
    </p>
  );
}

function Head({ detail, req, requestedId, asOf, rules }: DetailProps) {
  const rule = rules.platformRules.reelshort;
  return (
    <section className="border-line bg-panel rounded-lg border px-5 py-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <HeadFacts detail={detail} requestedId={requestedId} asOf={asOf} />
        <div className="flex flex-col items-end gap-2 text-[12px]">
          <span className="text-helper">
            {rule ? `${rule.material} · ${rule.back}` : "规则未知"}
          </span>
          <Link
            prefetch={false}
            href={pickHref(req, { tab: req.from, rowKey: "", page: req.page })}
            className="text-helper hover:text-ink-1"
          >
            ← 返回{TAB_LABELS[req.from]}
          </Link>
        </div>
      </div>
      <MetricGroups row={detail.row} />
      <ValidityNote row={detail.row} />
    </section>
  );
}

const DAY_MS = 86_400_000;
const ISO_DAY = /^\d{4}-\d{2}-\d{2}$/;

/**
 * 曲线下的清理说明。曲线画的是 as_of 所在 UTC 日往前 SERIES_DAYS 天（含当天，与 fillCalendar 同一算法），
 * 清理日晚于画出来的第一天，才有画不出来的点；早于它的清理与这张图无关，不提。不像日期的值也不提。
 */
function trimmedNote(trimmedBefore: string | null, asOf: Date): string {
  if (!trimmedBefore || !ISO_DAY.test(trimmedBefore)) return "";
  const firstDay = new Date(asOf.getTime() - (SERIES_DAYS - 1) * DAY_MS)
    .toISOString()
    .slice(0, 10);
  return trimmedBefore > firstDay
    ? `早于 ${trimmedBefore} 的曲线点已清理。`
    : "";
}

function Curves({
  detail,
  asOf,
  seriesTrimmedBefore,
}: {
  detail: ReelshortDetail;
  asOf: Date;
  seriesTrimmedBefore: string | null;
}) {
  const { row, series } = detail;
  const trimmed = trimmedNote(seriesTrimmedBefore, asOf);
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Spark
        title="30 天销售指标原值"
        current={
          row.metricsValid === true
            ? formatUsd(row.revenueCents)
            : "未取得已校验值"
        }
        rows={series.map((s) => ({ day: s.day, value: s.revenueRaw }))}
        days={SERIES_DAYS}
        today={asOf}
        color="var(--chart-1)"
        note={`仅绘制已校验的 UTC 每日首次保留值；无效或历史未校验样本留空，不等于零。${trimmed}`}
      />
      <Spark
        title="累计推广人数"
        current={
          row.metricsValid === true
            ? formatInt(row.promotersCnt)
            : "未取得已校验值"
        }
        rows={series.map((s) => ({ day: s.day, value: s.promoters }))}
        days={SERIES_DAYS}
        today={asOf}
        color="var(--chart-2)"
        note={`仅绘制已校验值；持平不能排除同步延迟或上游修订。${trimmed}`}
      />
    </div>
  );
}

function ClicksSection({ clicks }: { clicks: ReelshortDetail["clicks"] }) {
  const maxClicks = Math.max(1, ...clicks.map((c) => c.human));
  return (
    <Section title="出站点击 · 近 14 天">
      <p className="text-helper mb-2 text-xs leading-relaxed">
        按 UTC 日期分组。仅列有记录日期；按 UA
        排除已识别爬虫，剩余请求不能确认均为真人。
      </p>
      {clicks.length === 0 ? (
        <p className="text-helper py-5 text-sm">近 14 天没有记录到出站点击。</p>
      ) : (
        <table className="w-full text-sm">
          <thead>
            <tr className="text-helper text-left text-xs">
              <th scope="col">UTC 日期</th>
              <th scope="col" className="text-right">
                过滤后次数
              </th>
              <th scope="col" aria-label="相对数量" />
              <th scope="col" className="text-right">
                已识别爬虫
              </th>
            </tr>
          </thead>
          <tbody>
            {clicks.map((c) => (
              <tr key={c.day} className="border-line border-t">
                <td className="py-2">{c.day.slice(5)}</td>
                <td className="text-right tabular-nums">
                  {formatInt(c.human)}
                </td>
                <td className="w-1/3 px-3">
                  <span
                    className="inline-block h-2 rounded-sm bg-[var(--chart-2)]"
                    style={{ width: `${(c.human / maxClicks) * 100}%` }}
                  />
                </td>
                <td className="text-right tabular-nums">{formatInt(c.bot)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Section>
  );
}

/** 账单上的 book_id 不是这部正典剧自己时注明：同组同语种的兄弟资源同日同类型的行才分得开 */
function SiblingId({
  bookId,
  canonicalId,
}: {
  bookId: string;
  canonicalId: string;
}) {
  if (bookId === canonicalId) return null;
  return (
    <span
      className="text-ink-dim ml-1 font-mono text-[11px]"
      title="账单上的 book_id：同组同语种的另一个资源，订单归到这部正典剧"
    >
      {bookId}
    </span>
  );
}

function OrderRows({
  bill,
  canonicalId,
}: {
  bill: readonly BillRow[];
  canonicalId: string;
}) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-helper text-left text-xs">
            <th scope="col">日期</th>
            <th scope="col">推广类型</th>
            <th scope="col" className="text-right">
              订单数
            </th>
            <th scope="col" className="text-right">
              合并的原始行
            </th>
          </tr>
        </thead>
        <tbody>
          {bill.map((b) => (
            <tr
              key={`${b.bookId}-${b.billDate}-${b.promotionType}`}
              className="border-line border-t"
            >
              <td className="py-2 whitespace-nowrap">{b.billDate}</td>
              <td>
                {b.promotionType}
                <SiblingId bookId={b.bookId} canonicalId={canonicalId} />
              </td>
              <td className="text-right tabular-nums">
                {formatInt(b.orderCnt)}
              </td>
              <td className="text-right tabular-nums">
                {formatInt(b.sourceRows)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function OrdersSection({
  bill,
  truncated,
  canonicalId,
}: {
  bill: readonly BillRow[];
  truncated: boolean;
  canonicalId: string;
}) {
  return (
    <Section title="订单明细（最近 50 行）">
      <p className="text-helper mb-2 text-xs leading-relaxed">
        同一天、同一推广类型的账单行合并为一行，只列订单数，本页不显示金额。账号短链与口令可以用于多个渠道，不能证明订单来自本站。
        {truncated ? "更早的订单日没有列出。" : ""}
      </p>
      {bill.length === 0 ? (
        <p className="text-helper py-5 text-sm">
          这个版本里没有该剧的订单记录；不代表完整历史订单为零。
        </p>
      ) : (
        <OrderRows bill={bill} canonicalId={canonicalId} />
      )}
    </Section>
  );
}

function SameTitleSection({
  detail,
  req,
}: {
  detail: ReelshortDetail;
  req: PickRequest;
}) {
  const { sameTitle, sameTitleTruncated } = detail;
  return (
    <Section title="同名剧场行（一律未核：同名不等于同剧）">
      {sameTitle.length === 0 ? (
        <p className="text-helper text-sm">
          九个剧场的剧单里没有对上这部剧的同名行。
        </p>
      ) : (
        <ul className="flex flex-col gap-1 text-sm">
          {sameTitle.map((s) => (
            <li key={s.rowKey} className="flex flex-wrap gap-x-3">
              <Link
                prefetch={false}
                href={rowHref(req, s.rowKey)}
                className="hover:underline"
              >
                {s.title}
              </Link>
              {s.titleCn ? (
                <span className="text-helper">{s.titleCn}</span>
              ) : null}
              <span className="text-helper">
                {PLATFORM_LABELS[s.platform]} · {s.lang || "语种未标"}
              </span>
              {s.offOn ? (
                <span className="bg-warning-surface text-warning-ink rounded-sm px-2 py-0.5 text-[11.5px] font-medium">
                  下架 {s.offOn}
                </span>
              ) : null}
            </li>
          ))}
        </ul>
      )}
      {sameTitleTruncated ? (
        <p className="text-helper mt-2 text-xs">
          只列了前 {sameTitle.length} 行，可能还有。
        </p>
      ) : null}
    </Section>
  );
}

interface DetailProps {
  detail: ReelshortDetail;
  req: PickRequest;
  /** 链接里的 book_id（可以不是正典 id） */
  requestedId: string;
  /** 版本的 as_of：上线天数与曲线的最后一天都按它算 */
  asOf: Date;
  rules: BoardRules;
  /**
   * pick_mirror.series_state.trimmed_before 原值（YYYY-MM-DD）：早于它的曲线点已清理。
   * 何时提由本组件按 asOf 判（落进曲线窗口才提），页面原样传，不用自己比较；没有就传 null
   */
  seriesTrimmedBefore?: string | null;
}

export function ReelshortDetailView(props: DetailProps) {
  const { detail, req, asOf, seriesTrimmedBefore = null } = props;
  return (
    <div className="flex flex-col gap-4">
      <Head {...props} />
      <Section title="简介">
        {/* 宽屏分两栏铺满卡片：单栏限 80ch 时右边空一大块，不限宽又是 200 字一行没法读 */}
        {detail.row.description.trim() ? (
          <p className="text-ink-2 text-sm leading-[1.65] whitespace-pre-line lg:columns-2 lg:gap-10">
            {detail.row.description.trim()}
          </p>
        ) : (
          <p className="text-ink-dim text-sm">上游未给简介</p>
        )}
      </Section>
      <Curves
        detail={detail}
        asOf={asOf}
        seriesTrimmedBefore={seriesTrimmedBefore}
      />
      <div className="grid gap-4 lg:grid-cols-2">
        <ClicksSection clicks={detail.clicks} />
        <OrdersSection
          bill={detail.bill}
          truncated={detail.billTruncated}
          canonicalId={detail.row.id}
        />
      </div>
      {detail.postedRecords.length ? (
        <Section title={`我们的发布记录 · ${detail.postedRecords.length} 条`}>
          <div className="flex flex-col gap-3">
            {detail.postedRecords.map((p) => (
              <PostedRecordCard key={p.sd} p={p} req={req} />
            ))}
          </div>
        </Section>
      ) : null}
      <SameTitleSection detail={detail} req={req} />
    </div>
  );
}
