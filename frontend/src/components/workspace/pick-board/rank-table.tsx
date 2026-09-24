// PORTED_FROM: realshort@816ca2e src/components/admin/pick/rank-table.tsx
// 本地改动：RankTable 加 rules，四处资源格与信号标签都按版本规则判（原来归在「只改 import」，实际要改写）；
// 表的标题改读 rules.basisLabels；rs_bill 与 rs_ledger 两条说明重写：本页不显示金额，分成榜按 RealShort 导出的
// 名次（bill_rank）排，「分成对账」改叫「订单对账」；「备用资源」列头说明写明网盘只说有没有。
import {
  GROWTH_LIMIT,
  isDailyRank,
  isRsRank,
  type PickRequest,
  type RankKey,
  type RsRank,
  type TheaterBasis,
} from "@/core/pick-board/request";
import type { BoardRules } from "@/core/pick-board/rules";
import type { RankMeta, RankRow } from "@/server/pick-board";

import {
  DateCell,
  Dim,
  EpisodesCell,
  KindCell,
  LangCell,
  PickupCell,
  ResourceCell,
  TD,
  TextOrDim,
  TitleCell,
} from "./cells";
import { HeadCell, TableWrap, TR } from "./rows-table";
import { RankSpark, WeekGrid } from "./spark";

/**
 * 榜单 tab 的表：每张榜只看它自己的原样，列集合按榜的种类切换，与 artifact 的 renderRank 逐列相同。
 * 【一律含已下架的行】：那一天的榜是历史事实，抠掉后来下架的剧榜上就出现一个洞；行上标「下架」提醒。
 */
const SPARK_DAYS = 30;
const GRID_WEEKS = 20;

type Head = { text: string; hint?: string; right?: boolean };

const EPISODES_HEAD: Head = {
  text: "集数 · 起付费",
  hint: "总集数 · 第几集起付费；没有起付费集的只显示集数",
  right: true,
};
const COMMON_TAIL: Head[] = [
  { text: "语言" },
  { text: "类型 · 频道" },
  EPISODES_HEAD,
];
const TAIL: Head[] = [
  { text: "取货" },
  {
    text: "备用资源",
    hint: "剧单附没附网盘（链接不同步到本页），以及这个剧场的 YouTube 条件与下架记录",
  },
];

function dailyHeads(): Head[] {
  return [
    { text: "#", right: true },
    { text: "剧" },
    {
      text: "近 30 榜名次",
      hint: "横轴是最近 30 个有榜单的日子，缺榜的日子不占位；纵轴 #1 在上。相邻两次上榜之间隔了别的榜单日就是掉过榜，用虚线跨过去。实心大点是当前选中的那天。",
    },
    { text: "当日备注", hint: "剧场在榜单里写的字，原文" },
    { text: "上榜天数", hint: "累计不是连续，按全部日榜统计", right: true },
    { text: "最高", right: true },
    { text: "首次上榜" },
    { text: "语言" },
    EPISODES_HEAD,
    ...TAIL,
  ];
}

function headsFor(kind: TheaterBasis): Head[] {
  if (isDailyRank(kind)) return dailyHeads();
  if (kind === "kw")
    return [
      { text: "剧" },
      {
        text: "近 20 周上榜",
        hint: "每格一周，最近 20 个有周榜的周，缺榜的周不占位；实心格是上榜，折线是这段时间里的累计上榜周数，描边格是当前选中的周。",
      },
      { text: "上榜周数", right: true },
      { text: "最近一周" },
      ...COMMON_TAIL,
      ...TAIL,
    ];
  if (kind === "sm" || kind === "mg")
    return [
      { text: "评级" },
      { text: "剧" },
      ...COMMON_TAIL,
      { text: "日期", hint: "剧单里的推荐 / 上新日期" },
      ...TAIL,
    ];
  return [
    { text: "剧" },
    kind === "dbn"
      ? { text: "运营备注" }
      : kind === "gn"
        ? { text: "推荐理由" }
        : { text: "入榜日期" },
    ...COMMON_TAIL,
    { text: "剧单日期", hint: "剧单里的推荐 / 上新日期" },
    ...TAIL,
  ];
}

/** ReelShort 七张榜加订单对账的口径；前六条与 artifact 的 renderRank 逐字相同，分成榜与对账两条按本页重写 */
const RS_NOTES: Record<RsRank, string> = {
  rs_rr:
    "ReelShort 上游 recent_revenue 原值，全平台 30 天口径，单位与范围待核验，不是我方收入，只能横向比。口径与原观测台逐字相同。",
  rs_growth: `涨幅榜：ReelShort 全部行按所选增量排的前 ${GROWTH_LIMIT}，共用同一份数据。只有对应基线快照存在的剧才进榜（较昨日要昨天有已校验快照，较 7 天前要 7 天前有）；缺快照的剧不进，所以快照攒够之前榜单是空的，不代表没有涨的剧。增量是滚动窗口的净变化，不是新增收入。`,
  rs_cand:
    "候选清单：和本站有过交集的剧，三选一——已保存搜索匹配（GSC 展示 > 0）、近 7 天有出站（排除已识别爬虫）、有预估订单。候选原因在剧名右边那一列。",
  rs_pc: "上游 promoters_cnt，累计推广过这部剧的人数，全局口径不是我们一家。",
  rs_clk:
    "近 7 天从付费墙点「去 App 看」的次数，已按 UA 排除已识别爬虫；数的是点击不是独立用户。",
  rs_gsc:
    "已保存的 GSC 展示数，网页维度与查询维度取较大者；首页「搜索最多」货架读的就是它。",
  rs_bill:
    "按 RealShort 导出的预估分成名次（bill_rank）排序，本页不显示金额；预估不是结算，账单按 (group_key, locale) 摊到兄弟行，列表只显示正典那一行。",
  rs_ledger:
    "订单对账：上游账单里有订单的行，同一天、同一部剧、同一推广类型合并为一行，只列订单数，本页不显示金额。「站内同日有出站」是启发式不是归因——短链与口令是上游发给这个账号的，贴在哪里都是同一个值。",
};

/* 请求的那一期没对上时照说，不静默回落（问答审计 P1-6） */
const DAY_MISSING = "请求的那天没有榜，显示的是最近一天。";
const WEEK_NOTICE: Record<RankMeta["weekResolution"], string> = {
  latest: "",
  exact: "",
  label: "",
  ambiguous:
    "这个周标签在不同年份各有一周，显示的是其中最近的那周，更早那周在「周」里按年份选。",
  missing: "请求的那一周没有榜，显示的是最近一周。",
};

function span(meta: RankMeta): string {
  return `${meta.days[meta.days.length - 1] ?? "?"} 至 ${meta.days[0] ?? "?"}`;
}

function dailyNote(kind: "kd" | "qc" | "qr", meta: RankMeta): string {
  const missing = meta.dayResolution === "missing" ? DAY_MISSING : "";
  const day = meta.day || "还没有榜";
  if (kind === "kd")
    return `KalosTV 每日高转化 TOP10，${day}。${missing}上榜天数是累计不是连续，与最高名次一样按 ${span(meta)} 的全部日榜统计；当日备注是剧场在榜单里写的字。`;
  return `鹊娱汇聚台的${kind === "qc" ? "「7 日转化率 Top25」" : "「7 日总收入 Top25」"}，${day}。${missing}统计的是鹊娱全体分销商在九个剧场的成交，跨剧场混排、只给名次不给数值、不能按剧场或语种筛，每日 10:30 更新；不是我们自己的成交，也与本站 ReelShort 分成无关。上榜天数与最高名次按 ${span(meta)} 我们采到的全部榜统计（采集从 2026-09-12 起，更早的没有）。`;
}

const THEATER_NOTES: Partial<Record<TheaterBasis, string>> = {
  sm: "ShortMax 投放榜单剧的评级，SSS 到 D；同档按剧单日期从新到旧。",
  mg: "MoboReels 剧单里的评级列，SSS 到 D；同档按剧单日期从新到旧。",
  smd: "ShortMax「每日推荐必看」表里的剧。",
  fh: "FlickReels 爆款剧单，按入榜日期从新到旧。",
  sh: "StarShort 高充值剧单，剧场原话「闭眼冲」。",
  gh: "GoodShort 重点推荐里标为爆款的剧，按推荐日期从新到旧。",
  gn: "GoodShort 重点推荐（理由原文：新剧推荐 / 站内高充值 / KOC高转化），按推荐日期从新到旧。",
  ghh: "GoodShort 历史高充值（剧单里的隐藏表），按日期从新到旧。",
  dbn: "DramaBox 剧单里带运营备注的剧：爆款、重点、高优排期、收入这类关键词。",
};

/** 每张榜的口径说明（页面把它印在表上方） */
export function rankNote(
  kind: RankKey,
  meta: RankMeta,
  bucketed = false,
): string {
  if (isRsRank(kind)) {
    const base = RS_NOTES[kind];
    return bucketed
      ? `${base} 已按上线分桶筛选：只筛选已知日期的样本，不代表全部新剧。`
      : base;
  }
  if (kind === "kd" || kind === "qc" || kind === "qr")
    return dailyNote(kind, meta);
  if (kind === "kw") {
    const w = meta.weeks.find((x) => x.start === meta.week);
    return `KalosTV 周热门榜单，${w ? `${w.week}（周起 ${w.start}）` : "还没有榜"}。${WEEK_NOTICE[meta.weekResolution]}榜内不分名次，按累计上榜周数排。`;
  }
  return THEATER_NOTES[kind] ?? "";
}

const num = `${TD} text-right text-[12px] tabular-nums whitespace-nowrap`;
const nw = `${TD} text-[12px] whitespace-nowrap`;

function KdCells({ row, meta }: { row: RankRow; meta: RankMeta }) {
  const p = row.signal.payload as {
    h?: unknown;
    best?: number;
    days?: number;
    first?: string;
  };
  const daysAsc = meta.days.slice(0, SPARK_DAYS).reverse();
  return (
    <>
      <td className={nw}>
        <RankSpark h={p.h} daysAsc={daysAsc} day={meta.day} />
      </td>
      <td className="max-w-[220px] px-3 py-2 align-top text-[12px]">
        {row.dayNote || <Dim />}
      </td>
      <td className={num}>
        {typeof p.days === "number" ? `${p.days} 天` : <Dim />}
      </td>
      <td className={num}>
        {typeof p.best === "number" ? `#${p.best}` : <Dim />}
      </td>
      <td className={nw}>
        <TextOrDim text={p.first} />
      </td>
    </>
  );
}

function KwCells({ row, meta }: { row: RankRow; meta: RankMeta }) {
  const p = row.signal.payload as { h?: unknown; w?: string; weeks?: number };
  const weeksAsc = meta.weeks
    .slice(0, GRID_WEEKS)
    .map((w) => w.start)
    .reverse();
  return (
    <>
      <td className={nw}>
        <WeekGrid
          h={p.h}
          weeksAsc={weeksAsc}
          week={meta.week}
          totalWeeks={p.weeks ?? 0}
        />
      </td>
      <td className={num}>{typeof p.weeks === "number" ? p.weeks : <Dim />}</td>
      <td className={nw}>
        <TextOrDim text={p.w} />
      </td>
    </>
  );
}

interface RowProps {
  row: RankRow;
  req: PickRequest;
  meta: RankMeta;
  kind: TheaterBasis;
  rules: BoardRules;
}

function GradeCell({ grade }: { grade: string }) {
  return (
    <td className={nw}>
      <span className="border-warning-line bg-warning-surface text-gold rounded-[4px] border px-[7px] py-[2px] font-mono text-[11.5px] font-medium tracking-[.06em]">
        {grade || "?"}
      </span>
    </td>
  );
}

/** 各种榜共用的尾巴：取货与资源格（资源格按版本规则判 YouTube） */
function TailCells({ row, req, rules }: RowProps) {
  return (
    <>
      <PickupCell row={row} req={req} />
      <ResourceCell row={row} rules={rules} />
    </>
  );
}

function RankTr(props: RowProps) {
  const { row, req, meta, kind } = props;
  const s = row.signal;
  const cls = `${TR} ${row.offOn ? "opacity-60" : ""}`;
  if (isDailyRank(kind))
    return (
      <tr className={cls}>
        <td className={`${num} text-brand font-semibold`}>
          {row.dayRank !== null ? `#${row.dayRank}` : <Dim />}
        </td>
        <TitleCell row={row} req={req} />
        <KdCells row={row} meta={meta} />
        <LangCell row={row} />
        <EpisodesCell row={row} />
        <TailCells {...props} />
      </tr>
    );
  if (kind === "kw")
    return (
      <tr className={cls}>
        <TitleCell row={row} req={req} />
        <KwCells row={row} meta={meta} />
        <LangCell row={row} />
        <KindCell row={row} />
        <EpisodesCell row={row} />
        <TailCells {...props} />
      </tr>
    );
  const graded = kind === "sm" || kind === "mg";
  const hasNote = kind === "dbn" || kind === "gn";
  return (
    <tr className={cls}>
      {graded ? <GradeCell grade={s.grade} /> : null}
      <TitleCell row={row} req={req} />
      {graded ? null : (
        <td className={hasNote ? `${TD} max-w-[260px] text-[12px]` : nw}>
          <TextOrDim text={hasNote ? s.note : s.evidenceOn} />
        </td>
      )}
      <LangCell row={row} />
      <KindCell row={row} />
      <EpisodesCell row={row} />
      <DateCell row={row} />
      <TailCells {...props} />
    </tr>
  );
}

export function RankTable({
  rows,
  req,
  meta,
  kind,
  rules,
}: {
  rows: RankRow[];
  req: PickRequest;
  meta: RankMeta;
  kind: TheaterBasis;
  rules: BoardRules;
}) {
  return (
    <TableWrap>
      <table className="w-full min-w-[1100px] border-collapse text-left text-[13px]">
        <caption className="sr-only">{rules.basisLabels[kind]}</caption>
        <thead>
          <tr>
            {headsFor(kind).map((h) => (
              <HeadCell key={h.text} {...h} />
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <RankTr
              key={r.rowKey}
              row={r}
              req={req}
              meta={meta}
              kind={kind}
              rules={rules}
            />
          ))}
        </tbody>
      </table>
    </TableWrap>
  );
}
