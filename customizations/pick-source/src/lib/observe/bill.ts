import type { SourceAttempt } from "@/lib/observe/source-types";
import "server-only";
import { sql } from "drizzle-orm";
import { cpsBillDaily } from "@/db/schema";
import { getDb } from "@/db";
import { getAccountTerms, getBillEstimate } from "@/lib/cps/client";
import { planBillWindow } from "./bill-window";
import { collectBillPages } from "./bill-collection";
import {
  beginSource,
  failSource,
  readSources,
  sourceGuardSql,
  sourceSuccessSql,
} from "./source-state";

export interface BillIngestResult {
  fetched: boolean;
  backfill?: boolean;
  window?: { minDate: string; maxDate: string };
  rows?: number;
  upserted?: number;
  reason?: string;
}

/** 预估收益可被上游修正。完整分页与成功标记在同一事务中写入。 */
export async function ingestBill(now = new Date()): Promise<BillIngestResult> {
  let attempt: SourceAttempt | undefined;
  try {
    const previous = (await readSources()).bill;
    const plan = planBillWindow({
      now,
      lastFetchedAt: previous?.completedAt
        ? new Date(previous.completedAt)
        : null,
    });
    if (!plan.fetch && previous?.status === "success")
      return { fetched: false, reason: plan.reason };
    const window = plan.fetch
      ? plan
      : planBillWindow({ now, lastFetchedAt: null });
    if (!window.fetch) return { fetched: false, reason: window.reason };
    attempt = await beginSource("bill", now);
    const rows = await collectBillPages((page) =>
      getBillEstimate({
        minDate: window.minDate,
        maxDate: window.maxDate,
        page,
        limit: 40,
      }),
    );
    if (rows.some((r) => r.date < window.minDate || r.date > window.maxDate))
      throw new Error("收益日期超出请求窗口");
    const terms = await getAccountTerms();
    const db = getDb();
    const CHUNK = 500;
    const writes = [];
    for (let i = 0; i < rows.length; i += CHUNK) {
      writes.push(
        db
          .insert(cpsBillDaily)
          .values(
            rows.slice(i, i + CHUNK).map((r) => ({
              billDate: r.date,
              bookId: r.book_id,
              promotionType: r.promotion_type,
              promotionValue: r.promotion_value,
              bookTitle: r.book_title,
              orderCnt: r.order_cnt,
              revenueUsd: String(r.total_revenue),
              fetchedAt: now,
            })),
          )
          .onConflictDoUpdate({
            target: [
              cpsBillDaily.billDate,
              cpsBillDaily.bookId,
              cpsBillDaily.promotionType,
              cpsBillDaily.promotionValue,
            ],
            set: {
              bookTitle: sql`excluded.book_title`,
              orderCnt: sql`excluded.order_cnt`,
              revenueUsd: sql`excluded.revenue_usd`,
              fetchedAt: sql`excluded.fetched_at`,
            },
          }),
      );
    }
    // Neon HTTP batch 是一个事务：任一批次失败，所有行与成功标记一起回滚。
    const completion = db.execute(
      sourceSuccessSql("bill", attempt, {
        startDate: window.minDate,
        endDate: window.maxDate,
        timezone: "上游日期，时区未确认",
        rows: rows.length,
        ...terms,
      }),
    );
    await db.batch([
      db.execute(sourceGuardSql("bill", attempt)),
      ...writes,
      completion,
    ]);
    return {
      fetched: true,
      backfill: window.backfill,
      window: { minDate: window.minDate, maxDate: window.maxDate },
      rows: rows.length,
      upserted: rows.length,
    };
  } catch {
    if (attempt) await failSource("bill", attempt).catch(() => undefined);
    console.error("[observe] 预估收益采集未完成，保留既有数据");
    return { fetched: false, reason: "collection-incomplete" };
  }
}
