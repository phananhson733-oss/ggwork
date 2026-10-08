/**
 * 账单抓取窗口的判断逻辑。【纯函数，没有任何 server-only 依赖】——
 * 这样 `node --test` 能直接跑它，测的是行为而不是"代码长这样"
 * （同 lib/sitemap-request.ts 与 lib/gsc-request.ts 的既定做法）。
 */

/** 首次落库时回看多少天。上游账单不滚动，取不到的日期它自己会返回空。 */
export const BILL_BACKFILL_DAYS = 90;

/**
 * 日常增量回看多少天。
 *
 * 【不能只取昨天一天】：账单是次日结算的，而"次日"是上游的口径不是我们的；
 * 一次抓取失败、一次部署窗口错过，那一天就永远缺着，而页面上只是少一笔钱、
 * 不报任何错。回看 7 天让每一天有 7 次补齐机会，代价是每次多几十行 upsert。
 */
export const BILL_REFRESH_DAYS = 7;

export interface BillWindowInput {
  /** 当前时间 */
  now: Date;
  /** 库里最近一次抓取时间，从未抓过时给 null */
  lastFetchedAt: Date | null;
}

export type BillWindowPlan =
  | { fetch: false; reason: "already-fetched-today" }
  | { fetch: true; minDate: string; maxDate: string; backfill: boolean };

/** UTC 的 YYYY-MM-DD。账单日期用 UTC，与上游给的字符串同口径。 */
export function utcDay(date: Date): string {
  return date.toISOString().slice(0, 10);
}

/** 在 UTC 日历上往前推 n 天 */
export function shiftUtcDay(date: Date, days: number): string {
  return utcDay(new Date(date.getTime() - days * 86_400_000));
}

/**
 * 决定这一轮同步要不要抓账单、抓哪个窗口。
 *
 * 【一天最多一次】。同步每 6 小时一轮，账单一天只结算一次，
 * 四轮都抓就是三次白打上游——而 CLAUDE.md 记着「看到 code 100000 先怀疑参数」
 * 那条坑的成因之一就是没必要的重试放大了一个必然失败的请求。
 */
export function planBillWindow({
  now,
  lastFetchedAt,
}: BillWindowInput): BillWindowPlan {
  if (lastFetchedAt && utcDay(lastFetchedAt) === utcDay(now)) {
    return { fetch: false, reason: "already-fetched-today" };
  }
  const backfill = lastFetchedAt === null;
  const missingDays = lastFetchedAt
    ? Math.max(
        0,
        Math.ceil((now.getTime() - lastFetchedAt.getTime()) / 86_400_000),
      )
    : BILL_BACKFILL_DAYS;
  const lookback = backfill
    ? BILL_BACKFILL_DAYS
    : Math.min(
        BILL_BACKFILL_DAYS,
        Math.max(
          BILL_REFRESH_DAYS,
          missingDays > BILL_REFRESH_DAYS
            ? missingDays + BILL_REFRESH_DAYS
            : BILL_REFRESH_DAYS,
        ),
      );
  return {
    fetch: true,
    backfill,
    minDate: shiftUtcDay(now, lookback),
    maxDate: utcDay(now),
  };
}
