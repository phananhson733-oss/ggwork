import "server-only";
import { sql } from "drizzle-orm";
import { getDb } from "@/db";
import type {
  ObserveSource,
  SourceDetails,
  SourceState,
  SourceAttempt,
} from "./source-types";

export async function readSources(): Promise<
  Partial<Record<ObserveSource, SourceState>>
> {
  const db = getDb();
  const exists = await db.execute(
    sql`SELECT to_regclass('observe_sources') AS name`,
  );
  if (!exists.rows[0]?.name) return {};
  const rows = await db.execute(
    sql`SELECT source, status, attempted_at, completed_at, details FROM observe_sources`,
  );
  return Object.fromEntries(
    rows.rows.map((r) => [
      r.source,
      {
        source: r.source,
        status: r.status,
        attemptedAt: new Date(String(r.attempted_at)).toISOString(),
        completedAt: r.completed_at
          ? new Date(String(r.completed_at)).toISOString()
          : null,
        details: r.details,
      },
    ]),
  ) as Partial<Record<ObserveSource, SourceState>>;
}

/** 首次尝试先持久化。缺少迁移则拒绝写入，避免产生无来源的新批次。 */
export async function beginSource(
  source: ObserveSource,
  at = new Date(),
): Promise<SourceAttempt> {
  const id = crypto.randomUUID();
  await getDb().execute(sql`
    INSERT INTO observe_sources(source, status, attempted_at, attempt_id) VALUES (${source}, 'running', ${at.toISOString()}, ${id})
    ON CONFLICT(source) DO UPDATE SET status = 'running', attempted_at = excluded.attempted_at, attempt_id = excluded.attempt_id
  `);
  return { id, at };
}

/** Lock ownership for the whole data transaction; superseded attempts must abort it. */
export function sourceGuardSql(source: ObserveSource, attempt: SourceAttempt) {
  return sql`SELECT 1 / CASE WHEN (SELECT attempt_id = ${attempt.id} FROM observe_sources WHERE source = ${source} FOR UPDATE) THEN 1 ELSE 0 END AS owned`;
}

/**
 * 这次 attempt 是否已被新的一次顶替（observe_sources 的 attempt_id 不再是它，或整行没了）。
 * 给分多个 batch 写库的来源（pick_catalog）在某一批失败之后判断原因用：sourceGuardSql 核验不过时报的是 1 / 0 的除零，
 * 读一次 attempt_id 才说得清。只读，不改任何来源的状态。
 */
export async function sourceSuperseded(
  source: ObserveSource,
  attempt: SourceAttempt,
): Promise<boolean> {
  const rows = await getDb().execute(
    sql`SELECT attempt_id FROM observe_sources WHERE source = ${source}`,
  );
  return rows.rows[0]?.attempt_id !== attempt.id;
}

export function sourceSuccessSql(
  source: ObserveSource,
  attempt: SourceAttempt,
  details: SourceDetails,
) {
  return sql`UPDATE observe_sources SET status = 'success', completed_at = now(), details = ${JSON.stringify(details)}::jsonb
    WHERE source = ${source} AND attempt_id = ${attempt.id}`;
}

export async function completeSource(
  source: ObserveSource,
  attempt: SourceAttempt,
  details: SourceDetails,
): Promise<void> {
  await getDb().execute(sourceSuccessSql(source, attempt, details));
}

export async function failSource(
  source: ObserveSource,
  attempt: SourceAttempt,
): Promise<void> {
  // Failure recording must survive cancellation of the collection scope.
  await getDb(AbortSignal.timeout(10_000)).execute(sql`UPDATE observe_sources SET status = 'failed'
    WHERE source = ${source} AND attempt_id = ${attempt.id}`);
}
