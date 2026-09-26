// PORTED_FROM: realshort@816ca2e src/components/admin/pick/spark.tsx
// 本地改动：改 import（几何从 @/core/pick-board/spark-geometry 取）；两张图的外框、刻度与折线拆成小组件（函数 <50 行），
// 画出来的 SVG 与原文件相同；GGWork 配色：序列用图表色 --chart-1 / --chart-2（品牌色是墨色，不画数据）。
import type { ReactNode } from "react";

import {
  SPARK_H,
  SPARK_W,
  rankSpark,
  weekGrid,
} from "@/core/pick-board/spark-geometry";

import { Dim } from "./cells";

/**
 * 榜单 tab 行内的两张小图，服务端直接吐 SVG（与观测台的 Spark 同一做法：不引图表库、不把数据再送一趟）。
 * 几何全在 core/pick-board/spark-geometry.ts 里算好，这里只管颜色——颜色一律走 token，这棵树不许出现十六进制。
 */
const LABEL_FONT = "var(--font-mono, monospace)";

type RankGeometry = NonNullable<ReturnType<typeof rankSpark>>;
type WeekGeometry = NonNullable<ReturnType<typeof weekGrid>>;

function SparkFrame({
  title,
  children,
}: {
  title: string;
  children: ReactNode;
}) {
  return (
    <svg
      viewBox={`0 0 ${SPARK_W} ${SPARK_H}`}
      className="block h-8 w-[140px] overflow-visible"
      role="img"
      aria-label={title}
    >
      <title>{title}</title>
      {children}
    </svg>
  );
}

function Label({
  x,
  y,
  children,
}: {
  x: number;
  y: number;
  children: ReactNode;
}) {
  return (
    <text
      x={x}
      y={y}
      fill="var(--ink-dim)"
      fontSize={8}
      fontFamily={LABEL_FONT}
    >
      {children}
    </text>
  );
}

/** #1 与 #10 两条参考线和它们的标签 */
function RankGuides({ g }: { g: RankGeometry }) {
  return (
    <>
      {[g.yTop, g.yBottom].map((y) => (
        <line
          key={y}
          x1={5}
          x2={g.xEnd}
          y1={y}
          y2={y}
          stroke="var(--line-strong)"
          strokeWidth={1}
        />
      ))}
      <Label x={g.xEnd + 3} y={g.yTop + 3}>
        #1
      </Label>
      <Label x={g.xEnd + 3} y={g.yBottom + 3}>
        #10
      </Label>
    </>
  );
}

function RankLine({ g }: { g: RankGeometry }) {
  return (
    <>
      {g.segs.map((s, i) => (
        <line
          key={i}
          x1={s.x1}
          y1={s.y1}
          x2={s.x2}
          y2={s.y2}
          stroke="var(--chart-1)"
          strokeWidth={1.5}
          strokeLinecap="round"
          strokeDasharray={s.gap ? "2 3" : undefined}
          opacity={s.gap ? 0.5 : 1}
        />
      ))}
      {g.dots.map((d) => (
        <circle
          key={d.day}
          cx={d.x}
          cy={d.y}
          r={d.cur ? 3 : 1.7}
          fill="var(--chart-1)"
          stroke={d.cur ? "var(--surface)" : undefined}
          strokeWidth={d.cur ? 1.5 : undefined}
        />
      ))}
    </>
  );
}

export function RankSpark({
  h,
  daysAsc,
  day,
}: {
  h: unknown;
  daysAsc: readonly string[];
  day: string;
}) {
  const g = rankSpark(h, daysAsc, day);
  if (!g) return <Dim />;
  return (
    <SparkFrame title={g.title}>
      <RankGuides g={g} />
      <RankLine g={g} />
    </SparkFrame>
  );
}

function WeekCells({ g }: { g: WeekGeometry }) {
  return (
    <>
      {g.cells.map((c) => (
        <rect
          key={c.week}
          x={c.x}
          y={g.cellY}
          width={c.w}
          height={g.cellH}
          fill={c.on ? "var(--chart-1)" : "var(--raised)"}
          stroke={c.cur ? "var(--ink-1)" : c.on ? "none" : "var(--line)"}
          strokeWidth={c.cur ? 1.2 : 1}
        />
      ))}
    </>
  );
}

export function WeekGrid({
  h,
  weeksAsc,
  week,
  totalWeeks,
}: {
  h: unknown;
  weeksAsc: readonly string[];
  week: string;
  totalWeeks: number;
}) {
  const g = weekGrid(h, weeksAsc, week, totalWeeks);
  if (!g) return <Dim />;
  return (
    <SparkFrame title={g.title}>
      <WeekCells g={g} />
      <path
        d={g.path}
        fill="none"
        stroke="var(--chart-2)"
        strokeWidth={1.2}
        opacity={0.65}
      />
      <Label x={g.labelX} y={g.labelY}>
        {g.hit}/{g.n}
      </Label>
    </SparkFrame>
  );
}
