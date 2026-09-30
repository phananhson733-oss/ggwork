import "server-only";

import {
  trendsTableSchema,
  type TrendsTable,
} from "@/core/pick/trends-table-schema";

import { gatewayGet } from "./gateway";

/**
 * 简化版趋势雷达的表（2026-09-30 简化范围第 6 节第 4、7 项）：trends tab 只问 gateway 的
 * GET /api/pick/obs/trends-table，不读 pick_obs 视图、不钉镜像版本，也不给前端的库角色加授权。
 *
 * 回答按状态码分类：401 是会话在两次请求之间过期（页面转去登录）；别的状态码、超时、形状不对都是「读不了」，
 * 页面给固定文案。响应体不进结果也不进日志（gateway.ts）。
 */

export const TRENDS_TABLE_PATH = "/api/pick/obs/trends-table";

export type TrendsTableLoad =
  | Readonly<{ kind: "ok"; table: TrendsTable }>
  | Readonly<{ kind: "unauthenticated" }>
  | Readonly<{ kind: "unavailable" }>;

export async function loadTrendsTable(): Promise<TrendsTableLoad> {
  const answer = await gatewayGet(TRENDS_TABLE_PATH, trendsTableSchema);
  if (answer.ok) return { kind: "ok", table: answer.data };
  return answer.status === 401
    ? { kind: "unauthenticated" }
    : { kind: "unavailable" };
}
