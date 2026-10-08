import "server-only";
import {sql} from "drizzle-orm";
import {dramas} from "@/db/schema";

function published() {
  return sql`${dramas.detailSyncedAt} IS NOT NULL`;
}

const candidate = sql.raw("candidate");

/** CANONICAL_ORDER 的相关子查询表达；所有正典筛选路径必须共用这一份胜负语义。 */
function betterPublishedCandidate() {
  return sql`
    ${candidate}.detail_synced_at IS NOT NULL
    AND ${candidate}.group_key = ${dramas.groupKey}
    AND ${candidate}.locale = ${dramas.locale}
    AND (
      ${candidate}.chapter_count > ${dramas.chapterCount}
      OR (
        ${candidate}.chapter_count = ${dramas.chapterCount}
        AND ${candidate}.id < ${dramas.id}
      )
    )
  `;
}

/**
 * 用户可见集合只暴露每个 (groupKey, locale) 的正典行。
 *
 * 【导出给 lib/observe 用】：观测台的总览也要按正典去重（同一部剧的多个 book_id
 * 在榜上出现两次就是重复计数）。它写的是 raw SQL，所以那边的外层查询必须让
 * dramas 保持不加别名——这个片段渲染出来的是 "dramas"."group_key" 这种全名。
 * 【不要在 observe 里另抄一份 NOT EXISTS】，胜负规则只许有一处。
 *
 * 必须在语种、标签、标题和分页之前应用：先筛搜索词再挑正典，会在真正的正典行
 * 不匹配时把同组的旧 slug 重新捞出来。原始 slug/id 的承接与观看历史恢复刻意不用它。
 */
export function publicCanonical() {
  return sql`
    ${published()}
    AND NOT EXISTS (
      SELECT 1
      FROM ${dramas} AS ${candidate}
      WHERE ${betterPublishedCandidate()}
    )
  `;
}

