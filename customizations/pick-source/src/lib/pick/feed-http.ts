/**
 * 选剧工作台只读 feed 两条路由的 HTTP 外壳：v1 `/api/pick-feed` 与 v2 `/api/pick-feed/v2/[resource]`（方案 4.1、4.7）。
 * 【不许 import 任何 server-only 模块】——tests/pick-feed.test.ts 直接加载，用假的 load 跑完整张状态码矩阵。
 * 路由文件只读各自的 token、取当前时刻、把真正查库的 load 接进来；鉴权、参数、状态码与响应头都在这里。
 *
 * 状态码（两条路由相同，v2 的正文另带 version，4.1「每个响应都带 version」）：
 * - 404 not_found：token 没配（或只有空白）。整条路由惰性，不查库；
 * - 401 unauthorized：Bearer 不对。比较走 feedAuthorized 的定长常数时间；
 * - 400 bad_request：参数错。v2 另带一个固定的原因词（parseExportQuery 的 reason），不回显参数值；
 * - 409 source_changed：读完本页之后重算的 fingerprint 与 fp 不同；
 * - 503 source_busy：有来源正在写，带 Retry-After；
 * - 500 row_too_large（只有 v2）：单行超过 4 MB 硬上限，只带资源名与主键；
 * - 503 read_failed：load 抛错。只打一行固定日志，异常内容（可能含连接串、SQL、字段值）不进日志也不进正文。
 * 所有响应都是 cache-control: no-store。
 */
import { EXPORT_VERSION } from "./export-v2-map";
import { parseExportQuery, type ExportQuery, type ExportResult, type SourceFailure } from "./export-v2-page";
import { feedAuthorized, parseFeedQuery, type FeedPage, type FeedQuery } from "./feed-map";

/** v1 一页的结果：200 带整页，或者核对失败（只有带了 fp 才会有） */
export type FeedResult = { status: 200; page: FeedPage } | SourceFailure;

export interface FeedRouteDeps {
  /** PICK_FEED_TOKEN 原值（未 trim）；没配时整条路由 404 */
  token: string | undefined;
  /** 请求到达的时刻：as_of 的窗口按它算 */
  now: Date;
  load: (query: FeedQuery) => Promise<FeedResult>;
}

export interface ExportRouteDeps {
  /** PICK_EXPORT_TOKEN 原值（未 trim）；没配时整条路由 404。不与 v1 的 PICK_FEED_TOKEN 共用 */
  token: string | undefined;
  now: Date;
  load: (query: ExportQuery) => Promise<ExportResult>;
}

function noStore(body: unknown, status: number, headers: Record<string, string> = {}): Response {
  return Response.json(body, { status, headers: { "cache-control": "no-store", ...headers } });
}

/** 409 / 503 source_busy 的状态码与响应头；正文由调用方按各自的形状给 */
function sourceFailureInit(f: SourceFailure): { status: number; headers: Record<string, string> } {
  return f.status === 503 ? { status: 503, headers: { "retry-after": String(f.retryAfter) } } : { status: 409, headers: {} };
}

/** token 与 Bearer：没配 404，不对 401，都通过返回 null */
function gate(request: Request, rawToken: string | undefined, envelope: Record<string, unknown>): Response | null {
  const token = rawToken?.trim();
  if (!token) return noStore({ ok: false, ...envelope, error: "not_found" }, 404);
  if (!feedAuthorized(request.headers.get("authorization"), token)) return noStore({ ok: false, ...envelope, error: "unauthorized" }, 401);
  return null;
}

/** v1：/api/pick-feed。不带 as_of / fp 时与今天完全相同（不核对、不拦截），响应只多一个每页都有的 fingerprint */
export async function handleFeedGet(request: Request, deps: FeedRouteDeps): Promise<Response> {
  const denied = gate(request, deps.token, {});
  if (denied) return denied;
  const query = parseFeedQuery(new URL(request.url).searchParams, deps.now);
  if (!query) return noStore({ ok: false, error: "bad_request" }, 400);
  let result: FeedResult;
  try {
    result = await deps.load(query);
  } catch {
    console.error("[pick-feed] read failed");
    return noStore({ ok: false, error: "read_failed" }, 503);
  }
  if (result.status === 200) return noStore({ ok: true, ...result.page }, 200);
  const init = sourceFailureInit(result);
  return noStore({ ok: false, error: result.error }, init.status, init.headers);
}

/** v2：/api/pick-feed/v2/[resource]。resource 来自路径段，未知的资源名与其它参数错一样回 400（原因词 resource） */
export async function handleExportGet(request: Request, resource: string, deps: ExportRouteDeps): Promise<Response> {
  const envelope = { version: EXPORT_VERSION };
  const denied = gate(request, deps.token, envelope);
  if (denied) return denied;
  const parsed = parseExportQuery(resource, new URL(request.url).searchParams, deps.now);
  if (!parsed.ok) return noStore({ ok: false, ...envelope, error: "bad_request", reason: parsed.reason }, 400);
  let result: ExportResult;
  try {
    result = await deps.load(parsed.query);
  } catch {
    console.error("[pick-feed-v2] read failed");
    return noStore({ ok: false, ...envelope, error: "read_failed" }, 503);
  }
  switch (result.status) {
    case 200:
      /* 清洗命中数（hits）只给 manifest 的 meta.scrub 核对用，不进正文 */
      return noStore(result.body, 200);
    case 500:
      return noStore({ ok: false, ...envelope, error: result.error, resource: result.resource, key: result.key }, 500);
    default: {
      const init = sourceFailureInit(result);
      return noStore({ ok: false, ...envelope, error: result.error }, init.status, init.headers);
    }
  }
}
