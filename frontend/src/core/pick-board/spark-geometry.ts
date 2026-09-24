// PORTED_FROM: realshort@816ca2e src/lib/pick/spark-geometry.ts
// 本地改动：rankSpark 拆出 rankPoints / rankSegments、weekGrid 拆出 weekCells / cumulativePath（函数 <50 行）；
// 相邻段改按下一点取（原 :69 在 noUncheckedIndexedAccess 下报错），横轴下标改成先取再判（原来 `as number` 断言）。
// 几何结果不变。
/**
 * 榜单 tab 两张小图的几何：日榜「近 30 榜名次」折线、周榜「近 20 周上榜」格子。
 * 纯函数、不碰 DOM、不 import server-only，tests/unit/core/pick-board/spark-geometry.test.ts 直接跑它；
 * 组件层（components/workspace/pick-board/spark.tsx）只负责把这些数字吐成 SVG。
 *
 * 与 artifact 的 spark() / weekSpark() 同一套坐标（W=140 / H=32），断档规则也相同：
 * 横轴是「有榜单的日子」不是日历，缺榜的日子不占位；相邻两次上榜之间隔了别的榜单日
 * 就是掉过榜，用虚线跨过去，【不插值】——那几天它不在榜上，连成实线等于替剧场编名次。
 */

export const SPARK_W = 140;
export const SPARK_H = 32;
const PAD = 5;
/** 右侧给 #1 / #10 两个刻度字留的宽 */
const LABEL_W = 14;
/** 名次轴只画 1..10：日榜就是 TOP10，超出的钉在底线 */
const RANK_MAX = 10;

export type DayEntry = [string, number, string?];
export type WeekEntry = [string, string];

export interface RankSparkGeom {
  segs: { x1: number; y1: number; x2: number; y2: number; gap: boolean }[];
  dots: { x: number; y: number; cur: boolean; day: string; rank: number }[];
  yTop: number;
  yBottom: number;
  xEnd: number;
  title: string;
}

function r1(n: number): number {
  return Math.round(n * 10) / 10;
}

/** 只认形状对的条目：日期字符串 + 名次是正整数 */
export function dayEntries(h: unknown): DayEntry[] {
  if (!Array.isArray(h)) return [];
  return h.filter(
    (x): x is DayEntry =>
      Array.isArray(x) &&
      typeof x[0] === "string" &&
      typeof x[1] === "number" &&
      Number.isInteger(x[1]) &&
      x[1] >= 1,
  );
}

export function weekEntries(h: unknown): WeekEntry[] {
  if (!Array.isArray(h)) return [];
  return h.filter(
    (x): x is WeekEntry =>
      Array.isArray(x) && typeof x[0] === "string" && typeof x[1] === "string",
  );
}

interface RankPoint {
  /** 在横轴（有榜单的日子）上的下标 */
  i: number;
  rank: number;
  day: string;
}

/** 这一行落在横轴日子里的上榜点，按横轴顺序 */
function rankPoints(h: unknown, daysAsc: readonly string[]): RankPoint[] {
  const idx = new Map(daysAsc.map((d, i) => [d, i] as const));
  return dayEntries(h)
    .flatMap((x) => {
      const i = idx.get(x[0]);
      return i === undefined ? [] : [{ i, rank: x[1], day: x[0] }];
    })
    .sort((a, b) => a.i - b.i);
}

/** 相邻两点连一段；中间隔了别的榜单日就是掉过榜（gap，画虚线）。按下一点取，noUncheckedIndexedAccess 下不用断言 */
function rankSegments(
  pts: readonly RankPoint[],
  X: (i: number) => number,
  Y: (rank: number) => number,
): RankSparkGeom["segs"] {
  return pts.flatMap((a, k) => {
    const b = pts[k + 1];
    return b === undefined
      ? []
      : [
          {
            x1: X(a.i),
            y1: Y(a.rank),
            x2: X(b.i),
            y2: Y(b.rank),
            gap: b.i - a.i > 1,
          },
        ];
  });
}

/**
 * @param h       这一行日榜信号的 payload.h
 * @param daysAsc 最近 N 个有榜单的日子，旧到新
 * @param day     当前选中的那天（实心大点）
 * 少于两个横轴刻度、或这一行在这段日子里一次都没上榜，返回 null（组件画「—」）。
 */
export function rankSpark(
  h: unknown,
  daysAsc: readonly string[],
  day: string,
): RankSparkGeom | null {
  const n = daysAsc.length;
  if (n < 2) return null;
  const pts = rankPoints(h, daysAsc);
  if (pts.length === 0) return null;
  const xEnd = SPARK_W - PAD - LABEL_W;
  const X = (i: number) => r1(PAD + (i / (n - 1)) * (xEnd - PAD));
  const Y = (rank: number) =>
    r1(
      PAD +
        ((Math.min(rank, RANK_MAX) - 1) / (RANK_MAX - 1)) * (SPARK_H - 2 * PAD),
    );
  const segs = rankSegments(pts, X, Y);
  const dots = pts.map((p) => ({
    x: X(p.i),
    y: Y(p.rank),
    cur: p.day === day,
    day: p.day,
    rank: p.rank,
  }));
  const title = pts
    .slice(-14)
    .map((p) => `${p.day.slice(5)} #${p.rank}`)
    .join("  ");
  return { segs, dots, yTop: Y(1), yBottom: Y(RANK_MAX), xEnd, title };
}

export interface WeekGridGeom {
  cells: { x: number; w: number; on: boolean; cur: boolean; week: string }[];
  /** 累计上榜周数的折线 */
  path: string;
  /** 这 N 周里上榜了几周 */
  hit: number;
  n: number;
  cellY: number;
  cellH: number;
  labelX: number;
  labelY: number;
  title: string;
}

type WeekCell = WeekGridGeom["cells"][number] & {
  /** 到这一格为止累计上榜几周 */
  cum: number;
};

/** 每格一周；按周起认是否上榜，顺手记到这一格为止的累计上榜周数 */
function weekCells(
  h: unknown,
  weeksAsc: readonly string[],
  week: string,
  cw: number,
  pad: number,
): WeekCell[] {
  const on = new Set(weekEntries(h).map((x) => x[0]));
  let cum = 0;
  return weeksAsc.map((w, i) => {
    const hit = on.has(w);
    if (hit) cum += 1;
    return {
      x: r1(pad + i * cw),
      w: r1(Math.max(cw - 1.5, 1)),
      on: hit,
      cur: w === week,
      week: w,
      cum,
    };
  });
}

/** 累计上榜周数的折线：每格中点一个点，纵轴按总上榜周数归一 */
function cumulativePath(
  cells: readonly WeekCell[],
  cw: number,
  pad: number,
  cum: number,
): string {
  const max = Math.max(cum, 1);
  const top = pad;
  const bottom = SPARK_H - pad - 12;
  return cells
    .map(
      (c, i) =>
        `${i ? "L" : "M"}${r1(c.x + cw / 2)},${r1(bottom - (c.cum / max) * (bottom - top))}`,
    )
    .join("");
}

/**
 * @param h          这一行周榜信号的 payload.h（[[周起, 周标签]]）
 * @param weeksAsc   最近 N 个有周榜的周（周起日期），旧到新；按周起认不按周标签——标签不带年份，跨年重名
 * @param week       当前选中的周起日期（描边格）
 * @param totalWeeks 这一行累计上榜周数（payload.weeks），只进 title
 */
export function weekGrid(
  h: unknown,
  weeksAsc: readonly string[],
  week: string,
  totalWeeks: number,
): WeekGridGeom | null {
  const n = weeksAsc.length;
  if (n === 0) return null;
  const pad = 4;
  const labelW = 26;
  const cw = (SPARK_W - 2 * pad - labelW) / n;
  const cellH = 9;
  const cellY = SPARK_H - pad - cellH;
  const cells = weekCells(h, weeksAsc, week, cw, pad);
  const cum = cells.at(-1)?.cum ?? 0;
  const path = cumulativePath(cells, cw, pad, cum);
  return {
    cells: cells.map(({ x, w, on: o, cur, week: wk }) => ({
      x,
      w,
      on: o,
      cur,
      week: wk,
    })),
    path,
    hit: cum,
    n,
    cellY,
    cellH,
    labelX: SPARK_W - pad - labelW + 3,
    labelY: SPARK_H - pad - 1,
    title: `近 ${n} 周上榜 ${cum} 周，累计 ${totalWeeks} 周`,
  };
}
