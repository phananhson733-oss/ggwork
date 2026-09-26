// PORTED_FROM: realshort@816ca2e src/components/admin/pick/reelshort-table.tsx
// 本地改动：删掉「我方分成」一组的五列金额与它的列组头（本页不显示金额），在「指标 / 人数」后面加一列订单笔数；
// 表宽随之收窄；上线天数按版本的 as_of 算（now 改名 asOf）；公开页是 ReelShort 站的绝对地址，新标签打开（★38）；
// Link 加 prefetch={false}；表头与行拆成小组件（函数 <50 行），列的口径说明除金额外不变。
// GGWork 样式：名次用正文墨色与 tabular-nums（不用等宽），链接用 link 色，候选原因是语义 pill 尺寸。
import Link from "next/link";

import {
  candidateReasons,
  comparisonValue,
  formatInt,
  formatObservedAt,
  formatUsd,
  type Sort,
} from "@/core/pick-board/metrics";
import { reelshortRowKey, type PickRequest } from "@/core/pick-board/request";
import { dramaPath } from "@/core/pick-board/site";
import type { ObserveRow } from "@/server/pick-board";

import { TD } from "./cells";
import { ExternalLink } from "./links";
import {
  Delta,
  Dim,
  EpisodeCell,
  Num,
  PublishCells,
  TagChips,
  Th,
  ThGap,
  ThGroup,
  TitleCell,
} from "./reelshort-cells";
import { TableWrap, TR } from "./rows-table";
import { Empty, rowHref } from "./toolbar";

/**
 * ReelShort 七张榜共用的表（2026-09-11 从原观测台 rows-table 搬来）。
 *
 * 【一张表而不是七张】：七张榜看的是同一批列，差别只在过滤和排序。抄成几份的后果是
 * "候选清单里的日增和总览里的日增算法不一样"——两边各自看都正常，这是最难发现的那类错。
 *
 * 【表头两排】：上面一排是列组，把两种口径框开——平台指标（上游 recent_revenue，全平台 30 天滚动、原值）/
 * 推广人数（上游 promoters_cnt 累计 + 快照差分）。同一排表头里并排放，人一定会横着比，而它们单位、范围各不相同。
 * 下面一排每列的口径写在 hover 里；「较 N 天前」是快照差分（缺快照「—」）。本页不显示我方分成金额，只列订单笔数。
 */
const PLATFORM_HINT =
  "上游 book-detail 的 recent_revenue：全平台所有分销商的 30 天滚动销售额，上游原值、单位与范围待核验，不是我方收入。" +
  "只有「30 天」是真值；昨日 / 7 天 / 15 天三列是我们自己每天存快照算出来的净变化，是滚动窗口的变化不是那几天的销售额。上游没有累计值。";
const PROMOTERS_HINT =
  "上游 promoters_cnt：累计推广过这部剧的人数，全平台口径。新增三列是快照差分，快照 2026-09-10 起有效，所以 7 日新增 09-17 起、15 日新增 09-25 起才有数，之前显示「—」。";

function LeadHeads({ candidates }: { candidates: boolean }) {
  return (
    <>
      <Th right hint="当前排序下的名次。换一列排序，名次跟着变。">
        #
      </Th>
      <Th>剧</Th>
      {candidates ? (
        <Th hint="已保存搜索匹配（GSC 展示 > 0）、近 7 天有出站（排除爬虫）、有预估订单（账单 order_cnt > 0），三选一即进候选。">
          候选原因
        </Th>
      ) : null}
      <Th hint="上游 book-detail 的 publish_at。列表接口不给这个值（实测 120 条全为 0），所以只有拉过详情的剧才有；空的是还没轮到，不是上游没有。">
        上线日期
      </Th>
      <Th
        right
        hint="版本采集时点减去上线日期。未知时显示「—」而不是 0：0 的意思是「当天上线」，「—」的意思是「我们还不知道」。"
      >
        上线天数
      </Th>
      <Th
        right
        hint="总集数 · 免费边界内的集数。免费那半显示「?」时，是上游这两个字段对不上，不是 0 集免费。付费集 CPS 分销方拿不到，chapters 表里永远只有免费集。"
      >
        集数 · 免费
      </Th>
    </>
  );
}

function PlatformHeads() {
  return (
    <>
      <Th
        right
        hint="上游 recent_revenue 的原值，照抄不换算。单位与平台范围待核验，所以这里不写货币符号，也不要把它当成我方收入。"
      >
        30 天原值
      </Th>
      <Th
        right
        hint={
          "当前已保存值 减 昨天那一行快照。上游只给当前值不给历史，这个差值是我们自己每天存一行快照算出来的，缺快照显示「—」而不是 0。它是滚动 30 天窗口的净变化，不等于当天新增收入。\n\n" +
          "注意更新节奏：上游那个指标只在这部剧被重新拉详情时才变，而详情轮转约 600 部一天、全库 3.2 万部。所以多数剧在多数日子这里是 0，轮到它的那天则是把中间攒的量一次放出来。"
        }
      >
        较昨日
      </Th>
      <Th
        right
        hint="当前已保存值 减 7 天前那一行快照。比「较昨日」耐看：它跨过了详情轮转的空窗，不会因为这部剧这两天没被重新拉过详情就显示成 0。同样是滚动窗口的净变化，不是新增收入。"
      >
        较 7 天前
      </Th>
      <Th
        right
        hint="当前已保存值 减 15 天前那一行快照。快照 2026-09-10 起才有效，所以 09-25 前这一列全是「—」。同样是滚动窗口的净变化，不是新增收入。"
      >
        较 15 天前
      </Th>
    </>
  );
}

function PromoterHeads() {
  return (
    <>
      <Th
        right
        hint="上游 promoters_cnt，累计推广过这部剧的人数。和平台指标一样是上游的全局口径，不是我们一家。"
      >
        累计
      </Th>
      <Th right hint="推广人数比昨天那一行快照多出来的部分。缺快照显示「—」。">
        昨日新增
      </Th>
      <Th
        right
        hint="推广人数比 7 天前那一行快照多出来的部分。缺快照显示「—」（09-17 起才有）。"
      >
        7 日新增
      </Th>
      <Th
        right
        hint="推广人数比 15 天前那一行快照多出来的部分。缺快照显示「—」（09-25 起才有）。"
      >
        15 日新增
      </Th>
    </>
  );
}

function TrailHeads() {
  return (
    <>
      <Th
        right
        hint="30 天指标原值 ÷ 推广人数，看单个推广位平均摊到多少。分母为 0 时显示「—」。分子的单位与范围同样待核验，所以它只能横向比较，不能当金额读。"
      >
        指标 / 人数
      </Th>
      <Th
        right
        hint="上游账单里有订单的行，订单笔数合计（order_cnt）。本页只列笔数，不显示金额。"
      >
        订单
      </Th>
      <Th
        right
        hint="已保存的 GSC 展示数，网页维度与查询维度取较大者（同一次展示不能相加）。首页「搜索最多」那条货架读的就是这一列。"
      >
        搜索
      </Th>
      <Th
        right
        hint="滚动 168 小时内从付费墙点「去 App 看」的次数，已按 UA 排除已识别爬虫。它数的是点击不是独立用户。全站只有播放页付费墙一个出站口，所以这是离收入最近的一跳。"
      >
        7d 出站
      </Th>
      <Th>操作</Th>
    </>
  );
}

function HeadRows({ candidates }: { candidates: boolean }) {
  /* # / 剧 / (候选原因) / 上线日期 / 上线天数 / 集数 —— 这几列没有口径可分组 */
  const lead = candidates ? 6 : 5;
  return (
    <thead>
      <tr>
        <ThGap span={lead} />
        <ThGroup span={4} hint={PLATFORM_HINT}>
          平台指标 · 全平台 30 天滚动（上游原值）
        </ThGroup>
        <ThGroup span={4} hint={PROMOTERS_HINT}>
          推广人数 · 上游累计
        </ThGroup>
        <ThGap span={5} />
      </tr>
      <tr>
        <LeadHeads candidates={candidates} />
        <PlatformHeads />
        <PromoterHeads />
        <TrailHeads />
      </tr>
    </thead>
  );
}

function titleHint(r: ObserveRow, rank: boolean, sort: Sort): string {
  return [
    r.metricsValid === true ? "指标已校验" : "",
    `当前采集 ${formatObservedAt(r.syncedAt)}`,
    ...(rank
      ? [
          `对比采集 ${formatObservedAt(sort === "d1" || sort === "dp1" ? r.baseline1At : r.baseline7At)}`,
        ]
      : []),
  ]
    .filter(Boolean)
    .join(" · ");
}

function Reasons({ r }: { r: ObserveRow }) {
  return (
    <td className={`${TD} whitespace-nowrap`}>
      <span className="inline-flex flex-wrap gap-1">
        {candidateReasons(r).map((t) => (
          <span
            key={t}
            className="bg-success-surface text-success-ink rounded-sm px-2 py-0.5 text-[11.5px] font-medium"
          >
            {t}
          </span>
        ))}
      </span>
    </td>
  );
}

function MetricCells({ r }: { r: ObserveRow }) {
  const invalid = r.metricsValid === false;
  const eff =
    !invalid && r.promotersCnt > 0 ? r.revenueCents / r.promotersCnt : null;
  return (
    <>
      <Num>{invalid ? "未取得" : formatUsd(r.revenueCents)}</Num>
      <Num>
        <Delta current={r.revenueCents} previous={r.revenueCents1} />
      </Num>
      <Num>
        <Delta current={r.revenueCents} previous={r.revenueCents7} />
      </Num>
      <Num>
        <Delta current={r.revenueCents} previous={r.revenueCents15} />
      </Num>
      <Num>{invalid ? "未取得" : formatInt(r.promotersCnt)}</Num>
      <Num>
        <Delta current={r.promotersCnt} previous={r.promotersCnt1} />
      </Num>
      <Num>
        <Delta current={r.promotersCnt} previous={r.promotersCnt7} />
      </Num>
      <Num>
        <Delta current={r.promotersCnt} previous={r.promotersCnt15} />
      </Num>
      <Num>{eff === null ? <Dim /> : formatUsd(eff)}</Num>
    </>
  );
}

function SiteCells({ r, req }: { r: ObserveRow; req: PickRequest }) {
  return (
    <>
      <Num>{r.billOrders > 0 ? formatInt(r.billOrders) : <Dim>0</Dim>}</Num>
      <Num>
        {r.searchImpressions > 0 ? (
          formatInt(r.searchImpressions)
        ) : (
          <span className="text-helper">未匹配 / 未取得</span>
        )}
      </Num>
      <Num>{r.clicks7 > 0 ? formatInt(r.clicks7) : <Dim>0</Dim>}</Num>
      <td className={`${TD} text-[12px] whitespace-nowrap`}>
        <Link
          prefetch={false}
          href={rowHref(req, reelshortRowKey(r.id))}
          className="text-link hover:underline"
        >
          单剧 ›
        </Link>{" "}
        <ExternalLink
          href={dramaPath(r.locale, r.slug)}
          className="text-helper hover:text-link"
        >
          公开页 ↗
        </ExternalLink>
      </td>
    </>
  );
}

interface TableOptions {
  req: PickRequest;
  asOf: Date;
  rank: boolean;
  sort: Sort;
  candidates: boolean;
}

function ObserveTr({
  r,
  n,
  opts,
}: {
  r: ObserveRow;
  n: number;
  opts: TableOptions;
}) {
  const note =
    r.metricsValid === false
      ? "指标未取得有效值"
      : r.metricsValid === null
        ? "历史指标未保留校验记录"
        : "";
  return (
    <tr className={TR}>
      <td
        className={`${TD} text-ink-1 text-right text-[14px] font-medium whitespace-nowrap tabular-nums`}
      >
        #{n}
      </td>
      <TitleCell
        id={r.id}
        title={r.title}
        /* 【只留必须一眼看到的】指标【不正常】时的说明；溯源信息挪进 hover */
        note={note}
        hint={titleHint(r, opts.rank, opts.sort)}
        locale={r.locale}
        href={rowHref(opts.req, reelshortRowKey(r.id))}
        /* 上游标签只取前 6 个进行内，全部在证据页 */
        extra={<TagChips tags={r.tags} max={6} />}
      />
      {opts.candidates ? <Reasons r={r} /> : null}
      <PublishCells publishAt={r.publishAt} asOf={opts.asOf} />
      <EpisodeCell chapterCount={r.chapterCount} payStart={r.payStart} />
      <MetricCells r={r} />
      <SiteCells r={r} req={opts.req} />
    </tr>
  );
}

export function ReelshortTable({
  rows,
  asOf,
  req,
  rank = false,
  sort = "d7",
  candidates = false,
  offset = 0,
}: {
  rows: readonly ObserveRow[];
  /** 版本的 as_of：上线天数算到这一刻 */
  asOf: Date;
  req: PickRequest;
  /** 涨幅榜：只列有有效比较值的行 */
  rank?: boolean;
  sort?: Sort;
  candidates?: boolean;
  /** 分页的起始序号 */
  offset?: number;
}) {
  const shown = rank
    ? rows.filter((r) => comparisonValue(r, sort) !== null)
    : rows;
  if (shown.length === 0)
    return (
      <Empty>
        {rank
          ? "历史数据不足，暂不能排名。仅有有效比较数据的剧会进入榜单。"
          : "这个榜单在当前选择下没有记录。"}
      </Empty>
    );
  const opts: TableOptions = { req, asOf, rank, sort, candidates };
  return (
    <TableWrap>
      <table className="w-full min-w-[1400px] border-collapse text-[13px]">
        <HeadRows candidates={candidates} />
        <tbody>
          {shown.map((r, i) => (
            <ObserveTr key={r.id} r={r} n={offset + i + 1} opts={opts} />
          ))}
        </tbody>
      </table>
    </TableWrap>
  );
}
