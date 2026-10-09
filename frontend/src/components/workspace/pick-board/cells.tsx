// PORTED_FROM: realshort@816ca2e src/components/admin/pick/cells.tsx
// 本地改动：依据标签、日期口径与 YouTube 判定改读版本规则（rules 经 props 传入）；网盘一格只说有没有网盘，
// 有的话给 RealShort 证据页的外链（链接与提取码不进镜像）；ReelShort 的「分成 $」pill 改成「订单 N 笔」（★U26）；
// 公开页（dramaPath）是 ReelShort 站的绝对地址，新标签打开（★38）；Link 加 prefetch={false}；
// EvidenceLine 的 ev[0] 改成先取再判（noUncheckedIndexedAccess）。
// GGWork 配色：榜单名次 pill 用 info、负变化与失败用 danger、分级用 warning，品牌色不表状态；
// YouTube 条件改成语义 pill（YoutubePill，剧场规则 tab 与证据页共用）；pill 里的数字用 tabular-nums，不用等宽。
import Link from "next/link";
import type { ReactNode } from "react";

import {
  formatInt,
  formatObservedAt,
  formatUsd,
} from "@/core/pick-board/metrics";
import { youtubeStatus, type YoutubeRule } from "@/core/pick-board/platforms";
import {
  PLATFORM_LABELS,
  isDailyRank,
  reelshortId,
  type PickRequest,
} from "@/core/pick-board/request";
import type { BoardRules } from "@/core/pick-board/rules";
import type { PickPostedTag, PickRow, PickSignal } from "@/server/pick-board";

import { QueyuButton } from "./queyu-button";
import { ResourceLink } from "./resource-link";
import { rowHref } from "./toolbar";

/**
 * 行里反复出现的格子。信号 pill 与事实标签的文案与 artifact（xuanju.tmpl.html 的 sigHtml / factTags）逐条相同——
 * 「缺数据长什么样」只有一处定义：日期未知就写日期未知，不拿剧单日期代填。
 * 样式照 artifact 的 .sg（4px 圆角、7px 横距、11.5px）与 .tag，七种色调全部 token，这棵树不许出现十六进制。
 * ReelShort 行（row.rs）的六种 pill：30d 指标 / 推广 ±7 天 / 7 天净变化 / 订单 / 搜索 / 出站。
 * 依据标签与日期口径随版本走（rules.basisLabels / basisDateLabels），不在前端留一份。
 */

type Labels = Pick<BoardRules, "basisLabels" | "basisDateLabels">;

export function Dim({ children }: { children?: ReactNode }) {
  return <span className="text-helper">{children ?? "—"}</span>;
}

/** 文字为空（null / undefined / 空串）时显示「—」 */
export function TextOrDim({ text }: { text: string | null | undefined }) {
  return text ? <>{text}</> : <Dim />;
}

export const TD = "px-2.5 py-[7px] align-top";

type Tone =
  | "rank"
  | "down"
  | "grade"
  | "hot"
  | "week"
  | "legacy"
  | "demand"
  | "site";
const TONE: Record<Tone, string> = {
  rank: "border-transparent bg-info-surface text-info-ink",
  down: "border-transparent bg-danger-surface text-danger-ink",
  grade:
    "border-warning-line bg-warning-surface font-mono tracking-[.06em] text-warning-ink",
  hot: "border-transparent bg-warning-surface text-warning-ink",
  week: "border-line bg-raised text-ink-2",
  legacy: "border-line bg-transparent text-helper",
  demand: "border-transparent bg-success-surface text-success-ink",
  site: "border-line bg-raised text-helper",
};

export function Pill({
  tone,
  title,
  children,
}: {
  tone: Tone;
  title?: string;
  children: ReactNode;
}) {
  return (
    <span
      title={title}
      className={`inline-flex max-w-full items-center gap-1 rounded-sm border px-2 py-0.5 text-[11.5px] leading-[1.35] font-medium ${TONE[tone]}`}
    >
      {children}
    </span>
  );
}

function Num({ children }: { children: ReactNode }) {
  return <b className="font-semibold tabular-nums">{children}</b>;
}

function signed(n: number): string {
  return `${n > 0 ? "+" : "−"}${formatInt(Math.abs(n))}`;
}

type RankPayload = { best?: number; days?: number; w?: string; weeks?: number };

function dated(s: PickSignal, from = 5, to?: number): string {
  return s.evidenceOn ? ` · ${s.evidenceOn.slice(from, to)}` : "";
}

/** 日榜与鹊娱两张榜共用的「#名次 · 上榜 N 天 · 最高 #M · 日期」 */
function dailyRankText(s: PickSignal, p: RankPayload): string {
  const days = p.days && p.days > 1 ? ` · 上榜 ${p.days} 天` : "";
  const best = p.best && s.rank && p.best < s.rank ? ` · 最高 #${p.best}` : "";
  return `#${s.rank ?? "?"}${days}${best}${dated(s)}`;
}

function pillText(s: PickSignal, labels: Labels): string {
  const p = s.payload as RankPayload;
  switch (s.kind) {
    case "kd":
      return `日榜 ${dailyRankText(s, p)}`;
    case "kw":
      return `周热门 ${p.w ?? ""}${p.weeks && p.weeks > 1 ? ` · ${p.weeks} 周` : ""}`;
    case "qc":
    case "qr":
      return `${s.kind === "qc" ? "鹊娱转化" : "鹊娱收入"} ${dailyRankText(s, p)}`;
    case "sm":
      return s.grade;
    case "mg":
      return `Mobo ${s.grade}`;
    case "smd":
      return "每日推荐必看";
    case "fh":
      return `爆款剧单${dated(s)}`;
    case "sh":
      return "高充值 · 闭眼冲";
    case "gh":
      return `${s.note || "爆款推荐"}${dated(s)}`;
    case "gn":
      return `${s.note || "新剧推荐"}${dated(s)}`;
    case "ghh":
      return `历史高充值${dated(s, 2, 7)}`;
    case "dbn":
      return `备注 · ${s.note}`;
    default:
      return labels.basisLabels[s.kind];
  }
}

const PILL_TONE: Record<PickSignal["kind"], Tone> = {
  kd: "rank",
  kw: "week",
  qc: "rank",
  qr: "rank",
  sm: "grade",
  mg: "grade",
  smd: "hot",
  fh: "hot",
  sh: "hot",
  gh: "hot",
  gn: "week",
  ghh: "hot",
  dbn: "hot",
  clk: "hot",
  bill: "demand",
  gsc: "hot",
};

/** ReelShort 行的六种 pill，文案与 artifact 的 sigHtml 相同；有订单时只说笔数，不说金额（★U26） */
export function ReelshortPills({ rs }: { rs: NonNullable<PickRow["rs"]> }) {
  const d7 =
    rs.revenueCents7 === null ? null : rs.revenueCents - rs.revenueCents7;
  const p7 =
    rs.promotersCnt7 === null ? null : rs.promotersCnt - rs.promotersCnt7;
  return (
    <>
      <Pill
        tone="week"
        title={`上游 recent_revenue 原值，全平台 30 天口径，单位与范围待核验，不是我方收入；采集 ${formatObservedAt(rs.syncedAt)}`}
      >
        30d 指标{" "}
        <Num>
          {rs.metricsValid === false ? "未取得" : formatUsd(rs.revenueCents)}
        </Num>
      </Pill>
      <Pill
        tone="week"
        title={`上游 promoters_cnt，累计推广过这部剧的人数${p7 !== null ? `；较 7 天前快照 ${signed(p7)}` : ""}`}
      >
        推广 <Num>{formatInt(rs.promotersCnt)}</Num>
        {p7 !== null && p7 !== 0 ? ` · ${signed(p7)}` : ""}
      </Pill>
      {d7 !== null && d7 !== 0 ? (
        <Pill
          tone={d7 > 0 ? "demand" : "down"}
          title={`30 天指标较 7 天前快照（${formatObservedAt(rs.baseline7At)}）的净变化，不是新增收入`}
        >
          7 天 {signed(d7)}
        </Pill>
      ) : null}
      <SitePills rs={rs} />
    </>
  );
}

/** 本站三种信号：订单笔数（不说金额，★U26）、搜索展示、出站 */
function SitePills({ rs }: { rs: NonNullable<PickRow["rs"]> }) {
  return (
    <>
      {rs.billOrders > 0 ? (
        <Pill
          tone="demand"
          title="上游账单里有订单的行，订单笔数合计；本页不显示金额"
        >
          订单 {formatInt(rs.billOrders)} 笔
        </Pill>
      ) : null}
      {rs.searchImpressions > 0 ? (
        <Pill tone="hot" title="已保存的 GSC 展示数">
          搜索 {formatInt(rs.searchImpressions)}
        </Pill>
      ) : null}
      {rs.clicks7 > 0 ? (
        <Pill tone="hot" title="近 7 天付费墙出站，已排除已识别爬虫">
          出站 {formatInt(rs.clicks7)}
        </Pill>
      ) : null}
    </>
  );
}

function signalTitle(s: PickSignal, labels: Labels): string {
  const date = s.evidenceOn
    ? ` · ${labels.basisDateLabels[s.kind] || "日期"} ${s.evidenceOn}`
    : "";
  const note =
    s.kind === "dbn" || isDailyRank(s.kind) || !s.note ? "" : ` · ${s.note}`;
  return `${labels.basisLabels[s.kind]}${date}${note}`;
}

function sameTitlePills(row: PickRow): ReactNode[] {
  if (row.inSiteIds.length)
    return [
      <Pill
        key="site"
        tone="site"
        title={`ReelShort 片库有同名剧（${row.inSiteIds.length} 行），只是同名匹配`}
      >
        ReelShort 同名 · 未核
        {row.inSiteIds.length > 1 ? ` · ${row.inSiteIds.length} 行` : ""}
      </Pill>,
    ];
  if (row.siteOther)
    return [
      <Pill
        key="other"
        tone="legacy"
        title="ReelShort 片库有同名剧，但只有别的语种版本在售"
      >
        同名 · 他语种在售
      </Pill>,
    ];
  return [];
}

export function SignalPills({ row, rules }: { row: PickRow; rules: Labels }) {
  const parts: ReactNode[] = [
    ...(row.rs ? [<ReelshortPills key="rs" rs={row.rs} />] : []),
    ...row.signals.map((s) => (
      <Pill
        key={`${s.kind}-${s.ord}`}
        tone={PILL_TONE[s.kind]}
        title={signalTitle(s, rules)}
      >
        {pillText(s, rules)}
      </Pill>
    )),
    ...(row.legacyOnly
      ? [
          <Pill
            key="legacy"
            tone="legacy"
            title="旧域名 sitemap 收录过，ReelShort 片库里没有同名行；不代表这部剧上过 ReelShort"
          >
            旧站收录
          </Pill>,
        ]
      : []),
    ...sameTitlePills(row),
  ];
  if (parts.length === 0)
    return <Dim>{row.platform === "reelshort" ? "—" : "仅剧单收录"}</Dim>;
  return (
    <div className="flex max-w-[250px] min-w-[160px] flex-col items-start gap-1">
      {parts}
    </div>
  );
}

export interface Evidence {
  label: string;
  date: string | null;
  dk: string;
}

function reelshortEvidences(row: PickRow, labels: Labels): Evidence[] {
  const rs = row.rs;
  if (!rs) return [];
  const { basisLabels: l, basisDateLabels: d } = labels;
  return [
    ...(rs.clicks7 > 0
      ? [{ label: l.clk, date: rs.lastClickOn, dk: d.clk }]
      : []),
    ...(rs.billOrders > 0
      ? [{ label: l.bill, date: rs.lastBillOn, dk: d.bill }]
      : []),
    ...(rs.searchImpressions > 0
      ? [
          {
            label: l.gsc,
            date: rs.searchDataAt
              ? rs.searchDataAt.toISOString().slice(0, 10)
              : null,
            dk: d.gsc,
          },
        ]
      : []),
  ];
}

/** 一行的证据（依据 / 日期 / 日期口径），按日期从新到旧；与 artifact 的 evidences() 同一份口径 */
export function evidencesOf(row: PickRow, labels: Labels): Evidence[] {
  const fromSignals: Evidence[] = row.signals.map((s) => ({
    label: labels.basisLabels[s.kind],
    date: s.evidenceOn,
    dk: labels.basisDateLabels[s.kind] || "日期",
  }));
  return [...fromSignals, ...reelshortEvidences(row, labels)].sort((a, b) => {
    if (!a.date && !b.date) return 0;
    if (!a.date) return 1;
    if (!b.date) return -1;
    return b.date.localeCompare(a.date);
  });
}

/** 最近一条证据与它自己的日期口径；没有日期的写「日期未知」 */
export function EvidenceLine({ row, rules }: { row: PickRow; rules: Labels }) {
  const latest = evidencesOf(row, rules)[0];
  const line = "mt-1 font-mono text-[11px] text-helper";
  if (latest === undefined) {
    if (row.platform === "reelshort")
      return (
        <div className={line}>本站在售 · 无候选条件（搜索 / 出站 / 订单）</div>
      );
    return (
      <div className={line}>
        剧单收录 · {row.listedOn ? `剧单日期 ${row.listedOn}` : "日期未知"}
      </div>
    );
  }
  return (
    <div className={line}>
      {latest.label} ·{" "}
      {latest.date ? `${latest.dk} ${latest.date}` : "日期未知"}
    </div>
  );
}

type TagTone = "ok" | "warn" | "bad" | "neutral" | "dimmed" | "";
const TAG_TONE: Record<TagTone, string> = {
  "": "border-line text-ink-2",
  ok: "border-transparent bg-success-surface text-success-ink",
  warn: "border-transparent bg-warning-surface text-warning-ink",
  bad: "border-transparent bg-danger-surface text-danger-ink",
  neutral: "border-transparent bg-raised text-helper",
  dimmed: "border-line text-helper",
};

export function Tag({
  children,
  tone = "",
  title,
}: {
  children: ReactNode;
  tone?: TagTone;
  title?: string;
}) {
  return (
    <span
      title={title}
      className={`rounded-sm border px-2 py-0.5 text-[11px] ${TAG_TONE[tone]}`}
    >
      {children}
    </span>
  );
}

/** 上游指标字段的校验状态：已校验 / 未取得 / 没留校验记录 */
export function MetricsValidTag({ valid }: { valid: boolean | null }) {
  if (valid === true) return <Tag tone="ok">上游指标字段已校验 · 单位待核</Tag>;
  if (valid === false) return <Tag tone="bad">上游指标字段未取得</Tag>;
  return <Tag tone="dimmed">未保留校验记录</Tag>;
}

/** 事实标签：剧场声明 / 上游指标校验 / 同名未核 / 同名·他语种在售 / 旧站收录·非需求证据 / 日期未知，加发布记录标签 */
export function FactTags({ row, rules }: { row: PickRow; rules: Labels }) {
  const undated =
    (row.signals.length > 0 || row.rs !== undefined) &&
    !evidencesOf(row, rules).some((e) => e.date);
  const tags: ReactNode[] = [
    row.rs ? (
      <MetricsValidTag key="valid" valid={row.rs.metricsValid} />
    ) : row.signals.length ? (
      <Tag key="claim">剧场声明</Tag>
    ) : null,
    row.inSiteIds.length ? (
      <Tag key="site" tone="neutral">
        同名未核
      </Tag>
    ) : null,
    row.siteOther ? (
      <Tag key="other" tone="dimmed">
        同名 · 他语种在售
      </Tag>
    ) : null,
    row.legacyOnly ? (
      <Tag key="legacy" tone="dimmed">
        旧站收录 · 非需求证据
      </Tag>
    ) : null,
    undated ? (
      <Tag key="nodate" tone="dimmed">
        日期未知
      </Tag>
    ) : null,
    ...row.posted.map((p) => <PostedTagChip key={p.sd} tag={p} />),
  ].filter((t) => t !== null);
  if (tags.length === 0) return null;
  return <div className="mt-1 flex flex-wrap gap-1">{tags}</div>;
}

export function PostedTagChip({ tag }: { tag: PickPostedTag }) {
  /* 「已发」只数已回填 / 已公开（与 posted.py 同口径），待公开的另写一段，不混进已发数 */
  const sched = tag.schedCount ? ` · 另 ${tag.schedCount} 条待公开` : "";
  const text =
    tag.postCount > 0
      ? `已发 ${tag.postCount} 条${tag.viewsTotal ? ` · 播放 ${tag.viewsTotal.toLocaleString("en-US")}` : ""}${tag.lastPostOn ? ` · ${tag.lastPostOn.slice(5)}` : ""}${sched}`
      : `在选剧池 · ${tag.scheduled ? "已排期未发" : "未排期"}${sched}`;
  return (
    <Tag
      tone={tag.postCount > 0 ? "ok" : "warn"}
      title={`运营的飞书表「选剧池 / 发布记录」，剧名归一后对上（${tag.sd}${tag.life ? ` · ${tag.life}` : ""}），同名不等于同剧；证据页里有逐条帖子`}
    >
      {text}
    </Tag>
  );
}

export function TitleCell({ row, req }: { row: PickRow; req: PickRequest }) {
  const note = [row.titleCn, row.tags].filter(Boolean).join(" · ");
  return (
    <td className={`${TD} min-w-[200px]`}>
      <div className="text-[13px] leading-[1.35] font-semibold">
        <Link
          prefetch={false}
          href={rowHref(req, row.rowKey)}
          className="hover:text-link"
        >
          {row.title}
        </Link>
        {row.offOn ? (
          <span className="bg-warning-surface text-warning-ink ml-1.5 rounded-sm px-2 py-0.5 text-[11.5px] font-medium">
            下架 {row.offOn}
          </span>
        ) : null}
      </div>
      {note ? (
        <div className="text-helper mt-0.5 max-w-[280px] text-[11px]">
          {note}
        </div>
      ) : null}
      <div className="text-helper mt-0.5 text-[11px]">
        <span className="bg-raised text-ink-2 rounded-sm px-1 py-px font-semibold">
          {PLATFORM_LABELS[row.platform]}
        </span>{" "}
        · {row.sourceTable}
        {row.mergedRows > 1 ? ` · 由 ${row.mergedRows} 行合并` : ""}
        {row.rs ? ` · ${row.rs.locale} · ${row.rs.id}` : ""}
      </div>
    </td>
  );
}

export function SignalCell({ row, rules }: { row: PickRow; rules: Labels }) {
  return (
    <td className={TD}>
      <SignalPills row={row} rules={rules} />
      <EvidenceLine row={row} rules={rules} />
      <FactTags row={row} rules={rules} />
    </td>
  );
}

export function LangCell({ row }: { row: PickRow }) {
  return (
    <td className={`${TD} text-[12px] whitespace-nowrap`}>
      {row.lang || <Dim />}
    </td>
  );
}

/** 剧单的制作类型 · 频道。ReelShort 上游没有这两个字段，「—」上挂一句说明，免得被当成漏采 */
export function KindCell({ row }: { row: PickRow }) {
  const text = [row.kind, row.origin].filter(Boolean).join(" · ");
  const why =
    row.platform === "reelshort"
      ? "ReelShort 上游不给制作类型 / 频道（book-detail 没有这两个字段）；上游标签在剧名下面"
      : "剧单里没填";
  return (
    <td className={`${TD} text-ink-2 max-w-[200px] text-[12px]`}>
      {text || (
        <span className="text-helper cursor-help" title={why}>
          —
        </span>
      )}
    </td>
  );
}

/** 总集数 · 第几集起付费；没有起付费集的显示免费集数 */
export function EpisodesCell({ row }: { row: PickRow }) {
  return (
    <td
      className={`${TD} text-right text-[12px] whitespace-nowrap tabular-nums`}
    >
      {row.episodes === null ? (
        <Dim />
      ) : (
        <>
          {row.episodes}
          {row.payStart !== null ? (
            <span className="text-helper"> · 第 {row.payStart} 集起</span>
          ) : null}
        </>
      )}
    </td>
  );
}

export function DateCell({ row }: { row: PickRow }) {
  return (
    <td className={`${TD} text-[12px] whitespace-nowrap tabular-nums`}>
      {row.listedOn ?? <Dim />}
    </td>
  );
}

/** 取货：剧场行是「复制剧名并打开鹊娱」；ReelShort 行没有可下载素材，给证据页与公开页 */
export function PickupCell({ row, req }: { row: PickRow; req: PickRequest }) {
  if (row.platform !== "reelshort")
    return (
      <td className={TD}>
        <QueyuButton title={row.title} off={Boolean(row.offOn)} />
      </td>
    );
  return (
    <td className={`${TD} text-[12px] whitespace-nowrap`}>
      <Link
        prefetch={false}
        href={rowHref(req, row.rowKey)}
        className="text-link hover:underline"
      >
        单剧 ›
      </Link>
      {row.rs ? (
        <>
          {" "}
          <ResourceLink rowKey={row.rowKey} />
        </>
      ) : (
        <span
          className="text-helper ml-1"
          title={`book_id ${reelshortId(row.rowKey)}`}
        >
          指标未取到
        </span>
      )}
    </td>
  );
}

/** 剧单附没附网盘：链接与提取码不同步到镜像，有的话给这一行在 RealShort 选剧台的证据页 */
function PanNote({ row }: { row: PickRow }) {
  if (!row.hasPan) return <Dim>无网盘</Dim>;
  return (
    <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
      <span className="text-ink-2">有网盘</span>
      <ResourceLink rowKey={row.rowKey} />
    </div>
  );
}

const YOUTUBE_TONE: Record<YoutubeRule, string> = {
  ok: "bg-success-surface text-success-ink",
  only: "bg-info-surface text-info-ink",
  warn: "bg-warning-surface text-warning-ink",
  no: "bg-danger-surface text-danger-ink",
};

/**
 * YouTube 条件的语义 pill：可发 success、限剧单 info、慎用 warning、禁 danger，规则未知是中性；
 * 这一行发不了（禁，或限剧单而这一行不在剧单上）一律 danger。
 */
export function YoutubePill({
  kind,
  blocked = false,
  children,
}: {
  kind: YoutubeRule | null | undefined;
  blocked?: boolean;
  children: ReactNode;
}) {
  const tone = blocked
    ? YOUTUBE_TONE.no
    : kind
      ? YOUTUBE_TONE[kind]
      : "bg-raised text-helper";
  return (
    <span
      className={`inline-block rounded-sm px-2 py-0.5 text-[11.5px] leading-[1.35] font-medium ${tone}`}
    >
      {children}
    </span>
  );
}

/** 剧单附的网盘（只说有没有）+ 这个剧场的 YouTube 条件与下架 / 重新分销记录 */
export function ResourceCell({
  row,
  rules,
}: {
  row: PickRow;
  rules: Pick<BoardRules, "platformRules" | "youtubeLabels">;
}) {
  const yt = youtubeStatus(rules, row.platform, row.youtube);
  return (
    <td className={`${TD} text-[12px]`}>
      {row.platform === "reelshort" ? (
        <div className="text-ink-2">站内免费集可播 · 付费集在 App</div>
      ) : (
        <PanNote row={row} />
      )}
      <div className="mt-1">
        <YoutubePill kind={yt.kind} blocked={yt.blocked}>
          {yt.label}
        </YoutubePill>
      </div>
      {row.reoffNote ? (
        <div className="text-warning-ink mt-0.5 text-[11px]">
          {row.reoffNote}
        </div>
      ) : null}
    </td>
  );
}
