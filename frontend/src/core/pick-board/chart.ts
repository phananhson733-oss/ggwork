// PORTED_FROM: realshort@816ca2e src/lib/observe/chart.ts
// 本地改动：buildChart 拆出 valueRange / traceSeries 两个小函数（函数 <50 行）；断口虚线的上一个点改成先取再判
// （原来 `as ChartPoint` 断言）。几何结果不变。
/**
 * 折线图的几何计算。【纯函数，服务端直接算好 path 吐进 SVG】——
 * 观测台的图是静态的，没必要为它引一个图表库或者把数据再送一趟到客户端。
 */

export interface ChartPoint {
  /** YYYY-MM-DD */
  day: string;
  value: number;
}

export interface ChartGeometry {
  /** 折线本体。断档处会断开成多段（M 开头） */
  line: string;
  /** 填充区域，与折线同形但落到基线 */
  area: string;
  /** 断档处的虚线，把两段折线连起来 */
  gap: string;
  /** 末点坐标，用来画那个强调圆点 */
  last: { x: number; y: number } | null;
  /** y 轴刻度，从上到下 */
  ticks: { y: number; value: number }[];
  /** x 轴刻度 */
  xLabels: { x: number; day: string }[];
  min: number;
  max: number;
}

export interface ChartOptions {
  width: number;
  height: number;
  padLeft: number;
  padRight: number;
  padTop: number;
  padBottom: number;
  tickCount?: number;
}

/** 纵轴量程：上下各留 6%；全序列同值时给 1 的量程（见 buildChart） */
function valueRange(points: readonly (ChartPoint | null)[]): {
  min: number;
  max: number;
} {
  const values = points
    .filter((p): p is ChartPoint => p !== null)
    .map((p) => p.value);
  const rawMin = values.length ? Math.min(...values) : 0;
  const rawMax = values.length ? Math.max(...values) : 0;
  const span = rawMax - rawMin;
  return {
    min: span === 0 ? rawMin - 0.5 : rawMin - span * 0.06,
    max: span === 0 ? rawMax + 0.5 : rawMax + span * 0.06,
  };
}

/** 逐点描出折线、填充区与断口虚线；缺的点断开，不插值 */
function traceSeries(
  points: readonly (ChartPoint | null)[],
  x: (i: number) => number,
  y: (v: number) => number,
  baseline: number,
): Pick<ChartGeometry, "line" | "area" | "gap" | "last"> {
  let line = "";
  let area = "";
  let gap = "";
  let open = false;
  let lastIdx = -1;
  let last: { x: number; y: number } | null = null;

  points.forEach((p, i) => {
    if (p === null) {
      open = false;
      return;
    }
    const px = x(i);
    const py = y(p.value);
    if (!open) {
      const prev = lastIdx >= 0 ? points[lastIdx] : null;
      if (prev) {
        gap += `M${x(lastIdx)},${y(prev.value)} L${px},${py} `;
      }
      line += `M${px},${py} `;
      area += `M${px},${baseline} L${px},${py} `;
      open = true;
    } else {
      line += `L${px},${py} `;
      area += `L${px},${py} `;
    }
    const next = points[i + 1];
    if (next === null || next === undefined) {
      area += `L${px},${baseline} Z `;
    }
    lastIdx = i;
    last = { x: px, y: py };
  });

  return { line: line.trim(), area: area.trim(), gap: gap.trim(), last };
}

/**
 * 把一段带缺口的时间序列变成 SVG path。
 *
 * 【缺的那天必须真的断开，不能插值】。快照缺一天的原因是那天同步没跑成，
 * 而不是数值没变——画成直线等于替上游编了一个数，而观测台的全部意义
 * 就是看真实变化。断口用虚线跨过去，让人知道那里没有数据。
 *
 * 【全序列同值时给一个 1 的量程】。否则 (v - min) / (max - min) 是 0/0，
 * 整条线的 y 会变成 NaN，SVG 静默画不出来——页面不报错，图就是空的。
 */
export function buildChart(
  points: readonly (ChartPoint | null)[],
  opts: ChartOptions,
): ChartGeometry {
  const { width, height, padLeft, padRight, padTop, padBottom } = opts;
  const tickCount = opts.tickCount ?? 4;
  const { min, max } = valueRange(points);

  const n = points.length;
  const x = (i: number) =>
    n <= 1 ? padLeft : padLeft + (i / (n - 1)) * (width - padLeft - padRight);
  const y = (v: number) =>
    padTop + (1 - (v - min) / (max - min)) * (height - padTop - padBottom);

  const ticks = Array.from({ length: tickCount + 1 }, (_, k) => {
    const value = min + ((max - min) * k) / tickCount;
    return { y: y(value), value };
  });

  const labelIdx =
    n <= 1
      ? [0]
      : [0, Math.floor((n - 1) / 3), Math.floor(((n - 1) * 2) / 3), n - 1];
  const xLabels = labelIdx
    .map((i) => ({ x: x(i), day: points[i]?.day ?? "" }))
    .filter((l) => l.day !== "");

  return {
    ...traceSeries(points, x, y, height - padBottom),
    ticks,
    xLabels,
    min,
    max,
  };
}

/**
 * 把稀疏的快照序列铺成连续的日历天，缺的天填 null。
 *
 * 【必须按日历铺开，不能拿到几行就画几个点】：快照缺三天时，
 * 直接画会把那三天压缩掉，曲线的横轴就不再是时间——一段两周的平缓期
 * 会看起来和一天的暴涨一样宽。
 */
export function fillCalendar(
  rows: readonly { day: string; value: number }[],
  days: number,
  today: Date,
): (ChartPoint | null)[] {
  const byDay = new Map(rows.map((r) => [r.day, r.value]));
  const out: (ChartPoint | null)[] = [];
  for (let i = days - 1; i >= 0; i -= 1) {
    const day = new Date(today.getTime() - i * 86_400_000)
      .toISOString()
      .slice(0, 10);
    const value = byDay.get(day);
    out.push(value === undefined ? null : { day, value });
  }
  return out;
}
