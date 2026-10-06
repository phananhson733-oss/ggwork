// 工作台新建（简化版趋势雷达，2026-09-30）：趋势表每行的近 30 天小曲线。照 reelshort-spark.tsx 的画法改小：几何取
// core/pick-board/chart.ts 的 buildChart，日历由 trends-table.sparkPoints 铺好，只画完整日。没有数据的日子断开，
// 断口用虚线跨过去，不插值、不画成 0。服务端算好 path 直接吐 SVG，不加 client。
import { buildChart, type ChartPoint } from "@/core/pick-board/chart";

const W = 120;
const H = 28;
const BOX = {
  width: W,
  height: H,
  padLeft: 2,
  padRight: 5,
  padTop: 4,
  padBottom: 4,
  tickCount: 1,
};

export function TrendsSpark({
  points,
  title,
}: {
  points: readonly (ChartPoint | null)[];
  title: string;
}) {
  const filled = points.filter((p) => p !== null).length;
  if (filled < 2)
    return (
      <span className="text-ink-dim text-[12px]" data-trends-spark="few">
        {filled === 0 ? "没有完整日的数据" : "只有 1 天有数据"}
      </span>
    );
  const g = buildChart(points, BOX);
  return (
    <svg
      viewBox={`0 0 ${W} ${H}`}
      width={W}
      height={H}
      className="block"
      role="img"
      aria-label={`${title}：近 ${points.length} 天里 ${filled} 天有数据`}
      data-trends-spark="line"
    >
      <path
        d={g.gap}
        fill="none"
        stroke="var(--ink-dim)"
        strokeWidth={1}
        strokeDasharray="2 3"
      />
      <path
        d={g.line}
        fill="none"
        stroke="var(--chart-1)"
        strokeWidth={1.5}
        strokeLinejoin="round"
        strokeLinecap="round"
      />
      {g.last ? (
        <circle cx={g.last.x} cy={g.last.y} r={2} fill="var(--chart-1)" />
      ) : null}
    </svg>
  );
}
