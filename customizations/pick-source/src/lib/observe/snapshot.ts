import "server-only";

import { sql } from "drizzle-orm";

import {
  beginSource,
  sourceGuardSql,
  sourceSuccessSql,
  failSource,
} from "./source-state";

import { getDb } from "@/db";

/**
 * 每日片库快照。
 *
 * 存在的唯一理由：上游【只给当前值，不给历史】。`recent_revenue` 是滚动 30 天
 * 销售额、`promoters_cnt` 是累计推广人数，两个都只有"此刻是多少"。
 * 观测台要回答的"这几天涨了多少"，不自己留快照就永远算不出来。
 */

/** 快照保留天数。90 天足够看季度趋势，也是用户拍板的口径。 */
export const OBSERVATION_RETENTION_DAYS = 90;

/**
 * 把当前 dramas 表的观测列固化成"今天"这一行。
 *
 * 【整件事在服务端做完，一行数据都不过线】。源表和目标表在同一个库里，
 * INSERT ... SELECT 是一次往返；改成"查回 3 万行再写回去"是来回两趟约 3 MB，
 * 而 Neon 的账单大头正是这种往返（CLAUDE.md「数据库额度是硬约束」）。
 *
 * 【ON CONFLICT DO NOTHING 是幂等闸】。同步每 6 小时一轮，当天第一轮写入、
 * 后三轮全部空转。所以快照口径固定是"每个 UTC 日的第一轮同步"，
 * 不会因为跑了几轮就变成一天四个值，也不需要额外查一次"今天写过没有"。
 *
 * 【只收已拉过详情的剧】：没有详情的剧不出现在任何页面上，
 * 它的 chapter_count / pay_start 还是列表接口那份不可信的 0。
 *
 * @returns 本轮真正写入的行数。当天第二轮起恒为 0。
 */
export async function snapshotDramas(): Promise<number> {
  const db = getDb();
  const attempt = await beginSource("snapshot");
  try {
    const insert = db.execute(sql`
    INSERT INTO drama_observations
      (observed_on, drama_id, recent_revenue_cents, promoters_cnt, chapter_count, pay_start, metrics_valid)
    SELECT to_char(timezone('UTC', now()), 'YYYY-MM-DD'),
           id, recent_revenue_cents, promoters_cnt, chapter_count, pay_start, metrics_valid
    FROM dramas
    WHERE detail_synced_at IS NOT NULL
    ON CONFLICT (observed_on, drama_id) DO NOTHING
  `);
    const result = await db.batch([
      db.execute(sourceGuardSql("snapshot", attempt)),
      insert,
      db.execute(
        sourceSuccessSql("snapshot", attempt, {
          startDate: attempt.at.toISOString().slice(0, 10),
          timezone: "UTC",
          scope: "First retained value per UTC day and resource",
        }),
      ),
    ]);
    return result[1].rowCount ?? 0;
  } catch (error) {
    await failSource("snapshot", attempt).catch(() => undefined);
    throw error;
  }
}

/**
 * 清掉超出保留期的快照。
 *
 * 【必须有】。一天 3 万行，不清理就是一年 1,100 万行、约 1.7 GB——
 * 而全库现在总共 214 MB。保留期本身是对外没有承诺的内部数据，
 * 与 outbound_clicks 那 24 个月不同（那个写在隐私政策里）。
 */
/**
 * 今天（UTC）是否已经写过快照。
 *
 * 主键 (observed_on, drama_id) 上的一次索引查询，给非首轮同步省掉整段
 * INSERT ... SELECT（实测 868ms）。它是省钱的捷径，不是幂等闸——幂等仍靠
 * snapshotDramas 里的 ON CONFLICT DO NOTHING，两轮同步撞在同一秒也写不出两份。
 */
export async function hasSnapshotToday(): Promise<boolean> {
  const db = getDb();
  const result = await db.execute(sql`
    SELECT 1 FROM drama_observations
    WHERE observed_on = to_char(timezone('UTC', now()), 'YYYY-MM-DD')
    LIMIT 1
  `);
  return result.rows.length > 0;
}

export async function pruneObservations(): Promise<number> {
  const db = getDb();
  const result = await db.execute(sql`
    DELETE FROM drama_observations
    WHERE observed_on < to_char(
      timezone('UTC', now()) - ${`${OBSERVATION_RETENTION_DAYS} days`}::interval,
      'YYYY-MM-DD')
  `);
  return result.rowCount ?? 0;
}
