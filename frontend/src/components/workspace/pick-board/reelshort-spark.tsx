// PORTED_FROM: realshort@816ca2e src/components/admin/pick/reelshort-spark.tsx
// 本地改动：只改 import（曲线几何从 @/core/pick-board/chart 取）；today 由页面传版本的 as_of，不取墙上时钟；
// 刻度、日期标签、曲线本体与样本不足的说明拆成小组件（函数 <50 行），画法不变。
import {
  buildChart,
  fillCalendar,
  type ChartGeometry,
  type ChartPoint,
} from "@/core/pick-board/chart";

/**
 * 单剧的 90 天曲线（2026-09-11 从原观测台单剧页搬来）。服务端算好 path 直接吐 SVG，不引图表库、不把数据再送一趟。
 *
 * 【断档画虚线不画直线】：快照缺一天说明那天同步没跑成，不是数值没变。
 * 连成直线等于替上游编了一个数。
 */
const W = 640;
const H = 190;
const PAD_LEFT = 58;
const PAD_RIGHT = 14;
const CHART_BOX = {
  width: W,
  height: H,
  padLeft: PAD_LEFT,
  padRight: PAD_RIGHT,
  padTop: 12,
  padBottom: 26,
};

function Ticks({ g, prefix }: { g: ChartGeometry; prefix: string }) {
  return (
    <>
      {g.ticks.map((t, i) => (
        <g key={i}>
          <line
            x1={PAD_LEFT}
            x2={W - PAD_RIGHT}
            y1={t.y}
            y2={t.y}
            stroke="var(--line)"
            strokeWidth={1}
          />
          <text
            x={50}
            y={t.y + 3.5}
            textAnchor="end"
            fill="var(--helper)"
            fontSize={10.5}
          >
            {prefix}
            {t.value.toLocaleString("en-US", {
              maximumFractionDigits: g.max - g.min < 10 ? 2 : 0,
            })}
          </text>
        </g>
      ))}
    </>
  );
}

function DayLabels({ g }: { g: ChartGeometry }) {
  const last = g.xLabels.length - 1;
  return (
    <>
      {g.xLabels.map((l, i) => (
        <text
          key={l.day}
          x={l.x}
          y={H - 8}
          textAnchor={i === 0 ? "start" : i === last ? "end" : "middle"}
          fill="var(--helper)"
          fontSize={10.5}
        >
          {l.day.slice(5)}
        </text>
      ))}
    </>
  );
}

/** 曲线本体：面积、断档虚线、实线与最后一点 */
function Curve({ g, color }: { g: ChartGeometry; color: string }) {
  return (
    <>
      <path d={g.area} fill={color} opacity={0.16} />
      <path
        d={g.gap}
        fill="none"
        stroke="var(--ink-dim)"
        strokeWidth={1.5}
        strokeDasharray="3 4"
      />
      <path
        d={g.line}
        fill="none"
        stroke={color}
        strokeWidth={2}
        strokeLinejoin="round"
        strokeLinecap="round"
      />
      {g.last ? (
        <circle
          cx={g.last.x}
          cy={g.last.y}
          r={4}
          fill={color}
          stroke="var(--panel)"
          strokeWidth={2}
        />
      ) : null}
    </>
  );
}

function Chart({
  g,
  title,
  filled,
  color,
  prefix,
}: {
  g: ChartGeometry;
  title: string;
  filled: number;
  color: string;
  prefix: string;
}) {
  return (
    <svg
      viewBox={`0 0 ${W} ${H}`}
      className="block h-auto w-full"
      role="img"
      aria-label={`${title}，${filled} 天有数据`}
    >
      <Ticks g={g} prefix={prefix} />
      <DayLabels g={g} />
      <Curve g={g} color={color} />
    </svg>
  );
}

/** 样本不足两点时不画图，只说为什么 */
function TooFew({ filled }: { filled: number }) {
  return (
    <p className="text-ink-dim py-10 text-center text-[13px]">
      {filled === 0
        ? "尚无已校验快照，历史未校验记录不计入趋势。"
        : "仅有一个数据点，不足以分析趋势。"}
    </p>
  );
}

export function Spark({
  title,
  current,
  rows,
  days,
  today,
  color,
  prefix = "",
  note,
}: {
  title: string;
  current: string;
  rows: readonly { day: string; value: number }[];
  days: number;
  /** 曲线的最后一天：版本的 as_of，不是墙上时钟 */
  today: Date;
  color: string;
  prefix?: string;
  note: string;
}) {
  const points: (ChartPoint | null)[] = fillCalendar(rows, days, today);
  const filled = points.filter((p) => p !== null).length;
  const g = buildChart(points, CHART_BOX);
  return (
    <div className="border-line bg-panel rounded-[10px] border px-4 pt-3.5 pb-2.5">
      <h4 className="flex items-baseline justify-between text-[13px] font-semibold">
        <span>{title}</span>
        <span className="tabular-nums">{current}</span>
      </h4>
      <p className="text-helper mb-1.5 text-[11.5px]">{note}</p>
      <p className="text-helper mb-2 text-[12px]">
        有效样本 {filled}/{days} 天
      </p>
      {filled < 2 ? (
        <TooFew filled={filled} />
      ) : (
        <Chart
          g={g}
          title={title}
          filled={filled}
          color={color}
          prefix={prefix}
        />
      )}
    </div>
  );
}
