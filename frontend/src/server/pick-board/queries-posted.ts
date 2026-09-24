// PORTED_FROM: realshort@816ca2e src/lib/pick/queries-posted.ts
// 本地改动：记录对上的 ReelShort 剧改从 rs_ids 取（原 dramas）；单条记录页的返回具名为 PostedRecordDetail。
// P3-3a 只有类型与签名，函数体、列清单与行转换由 P3-3 补。
import "server-only";

import { type RawPost } from "@/core/pick-board/catalog-lang";
import {
  type PickRequest,
  type Platform,
  type PostedState,
} from "@/core/pick-board/request";

/**
 * 发布记录 tab：运营「选剧池 / 发布记录」的全表视图，加账号台账与单条记录页。
 * 已发 = post_count > 0，已排期未发 = post_count = 0 且 sched_count > 0，未排期 = 两个都是 0，
 * 剧库未对上 = row_keys 与 drama_ids 都空。四个筛选与 chips 计数共用同一组条件。
 */

export interface PostedRecord {
  sd: string;
  feishuRecord: string;
  title: string;
  lang: string;
  platform: string;
  life: string;
  scheduled: boolean;
  onlineOn: string | null;
  why: string;
  note: string;
  archived: boolean;
  postCount: number;
  schedCount: number;
  firstPostOn: string | null;
  lastPostOn: string | null;
  viewsTotal: number;
  viewsCount: number;
  metricAt: string | null;
  sources: string[];
  cats: string[];
  who: string[];
  accounts: string[];
  createdOn: string | null;
  updatedOn: string | null;
  rowKeys: string[];
  dramaIds: string[];
  /** 帖子明细；url 只在以 https:// 开头时渲染成链接 */
  posts: RawPost[];
}

/** 记录对上的剧场行，只取链接与一行显示要用的列 */
export interface LinkedRow {
  rowKey: string;
  platform: Platform;
  lang: string;
  title: string;
  offOn: string | null;
}

/** 记录对上的 ReelShort 剧（rs_ids） */
export interface LinkedDrama {
  id: string;
  locale: string;
  slug: string;
  title: string;
}

export interface PostedLinks {
  rows: Map<string, LinkedRow>;
  dramas: Map<string, LinkedDrama>;
}

export interface PostedList {
  rows: PostedRecord[];
  total: number;
  hasMore: boolean;
  /** 各状态多少条（带当前搜索词，不带当前状态筛选）；"" 是全部 */
  counts: Record<PostedState, number>;
  links: PostedLinks;
}

export interface PostedRecordDetail {
  record: PostedRecord;
  links: PostedLinks;
}

export interface PostedStats {
  total: number;
  pubCount: number;
  postsSum: number;
  viewsSum: number;
  /** 帖子指标（播放数）最晚回填到哪天 */
  metricAt: string | null;
  importedAt: Date | null;
  accountCount: number;
}

export interface CatalogAccount {
  id: string;
  name: string;
  /** 账号主页；只在以 https:// 开头时渲染成链接 */
  url: string;
  grp: string;
  form: string;
  niche: string;
  status: string;
  fans: number | null;
  asOf: string | null;
}

const pending = (name: string) =>
  new Error(`pick-board: ${name} is not implemented yet (P3-3)`);

export async function loadPostedList(_req: PickRequest): Promise<PostedList> {
  throw pending("loadPostedList");
}

export async function loadPostedRecord(
  _sd: string,
): Promise<PostedRecordDetail | null> {
  throw pending("loadPostedRecord");
}

export async function loadPostedStats(): Promise<PostedStats> {
  throw pending("loadPostedStats");
}

export async function loadAccounts(): Promise<CatalogAccount[]> {
  throw pending("loadAccounts");
}
