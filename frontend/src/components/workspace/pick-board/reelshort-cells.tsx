// PORTED_FROM: realshort@816ca2e src/components/admin/pick/reelshort-cells.tsx
// 本地改动：删掉我方分成的金额格与 Delta 的金额模式（本页没有金额）；上线天数按版本的 as_of 算（PublishCells 的
// now 改名 asOf）；剧名格的 Link 加 prefetch={false}；
// 表头的 hover 包装抽成 Hinted；剧名格 id 行的 cursor-help 前的空格留在模板里（prettier 会削掉字符串里的前导空格）。
// GGWork 配色：TagChips 不再按文本散列着色，一律中性底（标签不是状态，语义色只给状态）；负增量用 danger。
import Link from "next/link";
import type { ReactNode } from "react";

import {
  ageInDays,
  delta,
  episodeAvailability,
  formatInt,
  freeEpisodeBoundary,
} from "@/core/pick-board/metrics";

import { TD } from "./cells";
import { TH } from "./rows-table";

/**
 * ReelShort 榜与订单对账表里反复出现的几个单元格（2026-09-11 从原观测台 cells 搬来）。
 * 抽出来是为了让「缺数据长什么样」只有一处定义——「0」和「没有快照算不出来」必须长得不一样。
 */

export function Num({ children }: { children: ReactNode }) {
  return (
    <td className={`${TD} text-right whitespace-nowrap tabular-nums`}>
      {children}
    </td>
  );
}

export function Dim({ children }: { children?: ReactNode }) {
  return <span className="text-ink-dim">{children ?? "—"}</span>;
}

/**
 * 增量。
 *
 * 【null 渲染成「—」而不是 0】：0 的意思是"量过了，没变"，
 * null 的意思是"那天没有快照"。混成一个数会让一部正在涨的新剧看起来是死的。
 */
export function Delta({
  current,
  previous,
}: {
  current: number;
  previous: number | null;
}) {
  const d = delta(current, previous);
  if (d === null) return <Dim>—</Dim>;
  if (d === 0) return <span className="text-ink-dim">0</span>;
  return (
    <span className={d > 0 ? "text-success-ink" : "text-danger-ink"}>
      {d > 0 ? "+" : "−"}
      {formatInt(Math.abs(d))}
    </span>
  );
}

/** 上线日期两列：日期本身 + 上线天数（到版本的 as_of 为止）。未知时两格都明说未知，不填 0。 */
export function PublishCells({
  publishAt,
  asOf,
}: {
  publishAt: Date | null;
  asOf: Date;
}) {
  const age = ageInDays(publishAt, asOf);
  return (
    <>
      <td className={`${TD} whitespace-nowrap tabular-nums`}>
        {publishAt ? (
          publishAt.toISOString().slice(0, 10)
        ) : (
          <span className="text-ink-dim">未知</span>
        )}
      </td>
      <td className={`${TD} text-right whitespace-nowrap tabular-nums`}>
        {/* 只印天数，不再跟一个分桶标签：分桶本身仍是筛选维度 */}
        {age === null ? (
          <span className="text-ink-dim">—</span>
        ) : (
          formatInt(age)
        )}
      </td>
    </>
  );
}

/**
 * 剧名格。`note` 是必须一眼看到的东西（候选原因、指标异常）；`hint` 是采集时间那类
 * 溯源信息，挂在 id 那一行的 title 上，鼠标停上去才出来。
 * 【采集时间刻意不再逐行印出来】常态下每一行都是「指标已校验 · 当前采集某时某分」，
 * 一屏十行就是十遍同一句话；异常态仍然走 note 直接可见。
 */
export function TitleCell({
  id,
  title,
  locale,
  href,
  note,
  hint,
  extra,
}: {
  id: string;
  title: string;
  locale: string;
  href?: string;
  note?: string;
  hint?: string;
  /** 标题下面那一行（上游标签 chips），没有就不占位 */
  extra?: ReactNode;
}) {
  return (
    <td className={`${TD} min-w-[260px]`}>
      <div className="font-semibold">
        {href ? (
          <Link prefetch={false} href={href} className="hover:text-link">
            {title}
          </Link>
        ) : (
          title
        )}
      </div>
      {extra ? <div className="mt-1">{extra}</div> : null}
      {note ? <div className="text-helper mt-1 text-[12px]">{note}</div> : null}
      <div
        className={`text-ink-dim font-mono text-[11px] ${hint ? "cursor-help" : ""}`}
        title={hint}
      >
        {locale ? `${locale} · ` : ""}
        {id}
      </div>
    </td>
  );
}

function Hinted({ hint, children }: { hint?: string; children: ReactNode }) {
  if (!hint) return <>{children}</>;
  return (
    <span
      title={hint}
      className="decoration-line-strong cursor-help underline decoration-dotted underline-offset-4"
    >
      {children}
    </span>
  );
}

/**
 * 表头。`hint` 是这一列的口径，鼠标停上去看。
 * 【用原生 title 而不是自建浮层】外层是 overflow-x-auto，绝对定位的浮层会被容器裁掉；
 * 【虚线下划线不是装饰】没有它就没人知道哪几列能 hover 出解释。
 */
export function Th({
  children,
  right = false,
  hint,
}: {
  children: ReactNode;
  right?: boolean;
  hint?: string;
}) {
  return (
    <th scope="col" className={`${TH} ${right ? "text-right" : "text-left"}`}>
      <Hinted hint={hint}>{children}</Hinted>
    </th>
  );
}

/**
 * 表头上面那一排「列组」：把同一口径的几列框在一起（平台指标 / 推广人数）。
 * 【口径不同的列必须分组标出来】全平台 30 天滚动销售额与推广人数单位、范围都不同，
 * 并排放在同一排表头里没有分组，人一定会横着比。
 */
export function ThGroup({
  children,
  span,
  hint,
}: {
  children: ReactNode;
  span: number;
  hint?: string;
}) {
  return (
    <th
      scope="colgroup"
      colSpan={span}
      className={`${TH} border-line border-l text-center tracking-normal normal-case`}
    >
      <Hinted hint={hint}>{children}</Hinted>
    </th>
  );
}

/** 空白占位的列组头（前面几列没有口径可分组），只为让下面那排表头对齐 */
export function ThGap({ span }: { span: number }) {
  return (
    <th
      scope="colgroup"
      colSpan={span}
      className={`${TH} normal-case`}
      aria-hidden="true"
    />
  );
}

/**
 * 上游标签 chips（tag / show_tag 恒为中文，与剧集语言无关）。
 * 【一律中性底】：标签不是状态，success / warning / danger / info 只留给状态，不拿来区分标签。
 */
export function TagChips({
  tags,
  max,
}: {
  tags: readonly string[];
  max?: number;
}) {
  const shown = max ? tags.slice(0, max) : tags;
  if (shown.length === 0) return null;
  return (
    <span className="inline-flex flex-wrap gap-1">
      {shown.map((t, i) => (
        <span
          key={`${t}-${i}`}
          className="bg-raised text-ink-2 rounded-sm px-2 py-0.5 text-[11.5px]"
        >
          {t}
        </span>
      ))}
      {max && tags.length > max ? (
        <span className="text-ink-dim text-[11px]">+{tags.length - max}</span>
      ) : null}
    </span>
  );
}

/**
 * 集数格：`总集数 · 免费集数`。完整说法在 title 里，格子里只留两个数。
 * 免费那半算不出来时显示「?」而不是 0：0 的意思是「一集都不免费」（payStart=1 的剧真实存在），
 * 「?」的意思是「上游这两个字段对不上」。
 */
export function EpisodeCell({
  chapterCount,
  payStart,
}: {
  chapterCount: number;
  payStart: number;
}) {
  const free = freeEpisodeBoundary(chapterCount, payStart);
  return (
    <td className={`${TD} text-right whitespace-nowrap tabular-nums`}>
      <span title={episodeAvailability(chapterCount, payStart)}>
        {chapterCount > 0 ? formatInt(chapterCount) : "—"}
        <span className="text-helper mx-1">·</span>
        <span className={free === null ? "text-helper" : ""}>
          {free === null ? "?" : formatInt(free)}
        </span>
      </span>
    </td>
  );
}
