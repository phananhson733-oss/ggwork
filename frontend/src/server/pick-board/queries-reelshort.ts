// PORTED_FROM: realshort@816ca2e src/lib/pick/queries-reelshort.ts
// 本地改动：ReelShort 分支改读镜像 rs_rows（导出时已算好候选条件与中文语种名，不再在 SQL 里拼语种表）；
// loadReelshortDetail 先经 rs_ids 把任意 id 归到正典 id，canonical_id 为 NULL 时返回 null；
// 来源状态取 meta.sources；同名行元素具名为 SameTitleRow。
// P3-3a 只有类型与签名，union 片段与行转换由 P3-3 补。
import "server-only";

import { type PostedRecord } from "./queries-posted";
import { type SameTitleRow } from "./queries-shared";
import { type DramaDetail } from "./rs-queries";
import { readSources } from "./source-state";

/**
 * ReelShort 作为第十个剧场：选剧 / 全部剧库 tab 的 FROM 是 catalog_rows UNION ALL rs_rows。
 * 证据页是 rs-queries 单剧页的四条查询，加同名剧场行与发布记录。
 */

export interface ReelshortDetail extends DramaDetail {
  /** 剧场剧单里对上这部剧的行（catalog_rows.in_site_ids 含它），一律标同名未核 */
  sameTitle: SameTitleRow[];
  /** sameTitle 取回条数等于 SAME_TITLE_LIMIT：可能还有没取到的 */
  sameTitleTruncated: boolean;
  /** 运营发布记录里对上这部剧的记录 */
  postedRecords: PostedRecord[];
}

/** ReelShort 行的证据页。id 是 book_id，可以不是正典 id（经 rs_ids 归到正典） */
export async function loadReelshortDetail(
  _id: string,
): Promise<ReelshortDetail | null> {
  throw new Error(
    "pick-board: loadReelshortDetail is not implemented yet (P3-3)",
  );
}

/** 页底「数据来源与口径」里各采集来源的状态 */
export const loadObserveSources = readSources;
