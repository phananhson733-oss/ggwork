import "server-only";
import { setTimeout as backoff } from "node:timers/promises";
import { checkSyncDeadline, getSyncSignal } from "@/lib/sync-deadline";

import {
  CpsApiError,
  allBookDataSchema,
  billDataSchema,
  bookDetailSchema,
  envelope,
  loginDataSchema,
  type CpsBillItem,
  type CpsBookDetail,
  type CpsBookListItem,
} from "./types";
import { localeFromCnName } from "./lang";

const BASE_URL = process.env.CPS_BASE_URL?.trim() || "https://cps.reelshort.com";
const APP = process.env.CPS_APP?.trim() || "reelshort";

/**
 * 文档写 book-detail 的 book_type 固定为 0，但线上实测（以及既有 Java 实现）用的是 1，
 * 且详情响应里回来的也是 1。这里默认 1，并在拿不到章节时自动回退到 0 重试一次。
 */
const DETAIL_BOOK_TYPE = Number(process.env.CPS_DETAIL_BOOK_TYPE?.trim() || 1);

/** 文档建议 limit 不超过 40 */
const MAX_PAGE_SIZE = 40;

/** accessToken 上游有效期约 30 天，这里保守缓存 12 小时 */
const TOKEN_TTL_MS = 12 * 60 * 60 * 1000;

interface CachedToken {
  token: string;
  expiresAt: number;
}

/**
 * 模块级缓存。Serverless 下每个实例独立，冷启动会多一次登录调用，可以接受；
 * 换成跨实例共享（Redis / DB）时只需替换这两个函数的实现。
 */
let tokenCache: CachedToken | null = null;
let inflightLogin: Promise<string> | null = null;
const syncLogins = new WeakMap<AbortSignal, Promise<string>>();

/** Only sync requests get this timeout; public playback keeps its existing behavior. */
function syncRequestSignal(): AbortSignal | undefined {
  const signal = getSyncSignal();
  signal?.throwIfAborted();
  return signal ? AbortSignal.any([signal, AbortSignal.timeout(30_000)]) : undefined;
}
let accountTerms: { ratio: number | null; billPeriod: string | null; termsFetchedAt: string | null } = { ratio: null, billPeriod: null, termsFetchedAt: null };

function readCredentials(): { account: string; password: string } {
  const account = process.env.CPS_ACCOUNT;
  const password = process.env.CPS_PASSWORD;
  if (!account || !password) {
    throw new Error(
      "缺少 CPS 凭据：请在 .env.local 设置 CPS_ACCOUNT 与 CPS_PASSWORD（不要提交到仓库）",
    );
  }
  return { account, password };
}

async function login(): Promise<string> {
  const { account, password } = readCredentials();
  const res = await fetch(`${BASE_URL}/api/v1/user/login`, {
    method: "POST",
    headers: { "content-type": "application/json", accept: "application/json" },
    body: JSON.stringify({
      channel_account: account,
      password,
      channel_type: "email",
    }),
    cache: "no-store",
    signal: syncRequestSignal(),
  });

  if (!res.ok) {
    throw new CpsApiError("user/login", res.status, `HTTP ${res.status}`);
  }
  const parsed = envelope(loginDataSchema).parse(await res.json());
  if (parsed.code !== 0 || !parsed.data) {
    throw new CpsApiError("user/login", parsed.code, parsed.msg || "登录失败");
  }
  const info = parsed.data.user_info;
  accountTerms = {
    ratio: info && info.reel_short_promotion_ratio > 0 && info.reel_short_promotion_ratio <= 100 ? info.reel_short_promotion_ratio : null,
    billPeriod: info?.bill_period_type || null,
    termsFetchedAt: new Date().toISOString(),
  };
  return parsed.data.accessToken;
}

/** 只暴露本次已认证读取的分成条款，不把 token 或账号资料传给展示层。 */
export async function getAccountTerms() {
  await getToken();
  return accountTerms;
}

/** 并发调用只会触发一次登录，其余等待同一个 promise */
async function getToken(forceRefresh = false): Promise<string> {
  checkSyncDeadline();
  const signal = getSyncSignal();
  const now = Date.now();
  if (!forceRefresh && tokenCache && now < tokenCache.expiresAt) {
    return tokenCache.token;
  }
  const pending = signal ? syncLogins.get(signal) : inflightLogin;
  if (pending) return pending;

  const request = login()
    .then((token) => {
      tokenCache = { token, expiresAt: Date.now() + TOKEN_TTL_MS };
      return token;
    })
    .finally(() => {
      if (signal) syncLogins.delete(signal);
      else inflightLogin = null;
    });

  if (signal) syncLogins.set(signal, request);
  else inflightLogin = request;
  return request;
}

/** 上游用这些 code 表示登录态失效，需要重新登录 */
const AUTH_ERROR_CODES = new Set([401, 403, 1001, 10001, 100001]);

/** Retry the same read after an explicit transient failure; never skip a page. */
async function post<T>(
  endpoint: string,
  body: Record<string, unknown>,
  parse: (raw: unknown) => T,
): Promise<T> {
  for (let retry = 0; ; retry++) {
    try {
      return await postAuthenticated(endpoint, body, parse);
    } catch (error) {
      const transient = error instanceof CpsApiError && (
        [429, 502, 503, 504].includes(error.code) ||
        (error.code === 100000 && error.message.endsWith(" service overloaded"))
      );
      if (!transient || retry >= 3) throw error;
      await backoff(1000 * 2 ** retry, undefined, { signal: getSyncSignal() });
    }
  }
}

/** Token expiry gets one independent authentication retry. */
async function postAuthenticated<T>(
  endpoint: string,
  body: Record<string, unknown>,
  parse: (raw: unknown) => T,
  attempt = 0,
): Promise<T> {
  const token = await getToken(attempt > 0);
  const res = await fetch(`${BASE_URL}${endpoint}`, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      accept: "application/json",
      "accept-language": "zh",
      authorization: `Bearer ${token}`,
    },
    body: JSON.stringify(body),
    cache: "no-store",
    signal: syncRequestSignal(),
  });

  if ((res.status === 401 || res.status === 403) && attempt === 0) {
    return postAuthenticated(endpoint, body, parse, attempt + 1);
  }
  if (!res.ok) {
    throw new CpsApiError(endpoint, res.status, `HTTP ${res.status}`);
  }

  const raw: unknown = await res.json();
  const code = extractCode(raw);
  if (code !== 0 && AUTH_ERROR_CODES.has(code) && attempt === 0) {
    return postAuthenticated(endpoint, body, parse, attempt + 1);
  }
  return parse(raw);
}

function extractCode(raw: unknown): number {
  if (raw && typeof raw === "object" && "code" in raw) {
    const code = (raw as { code: unknown }).code;
    if (typeof code === "number") return code;
  }
  return -1;
}

function unwrap<T>(
  endpoint: string,
  parsed: { code: number; msg: string; data?: T | null },
): T {
  if (parsed.code !== 0 || parsed.data == null) {
    throw new CpsApiError(
      endpoint,
      parsed.code,
      parsed.msg || "上游返回空数据",
    );
  }
  return parsed.data;
}

export interface ListBooksParams {
  page?: number;
  limit?: number;
  sort?: "time" | string;
  cursor?: string;
  /**
   * 语言过滤。传中文名（"英语"）或语言代码（"en"）都可以，内部统一转成代码。
   *
   * 【2026-09-01 实测的上游变更】：`language` 现在【只接受语言代码】。
   * 传中文名会让整个请求返回 **HTTP 400 + code 100000「系统繁忙，请稍后再试」**——
   * 那句文案是假的，它不是限流也不是故障，就是参数值不认识。
   * 同一个请求去掉 language 立刻 200，`language: "en"` 也 200 且过滤生效。
   *
   * 三个必需参数是 `app` / `sort` / `page`+`limit`，少任何一个也是同一个 400。
   * 所以【不要】把这个 400 当成上游挂了去重试，它重试多少次都是 400。
   */
  language?: string;
}

/**
 * 上游只认语言代码，但我们内部（CLI flag、`dramas.langCn`）到处用中文名。
 * 认不出来的值原样透传：上游若哪天又支持别的写法，不至于被这一层挡死。
 */
function toLanguageCode(language: string): string {
  return localeFromCnName(language)?.code ?? language;
}

export interface ListBooksResult {
  books: readonly CpsBookListItem[];
  nextCursor: string | null;
}

export async function listBooks(
  params: ListBooksParams = {},
): Promise<ListBooksResult> {
  const endpoint = "/api/v1/book/all-book";
  const body: Record<string, unknown> = {
    app: APP,
    sort: params.sort ?? "time",
    page: params.page ?? 1,
    limit: Math.min(params.limit ?? MAX_PAGE_SIZE, MAX_PAGE_SIZE),
  };
  if (params.cursor) body.cursor = params.cursor;
  if (params.language) body.language = toLanguageCode(params.language);

  const data = await post(endpoint, body, (raw) =>
    unwrap(endpoint, envelope(allBookDataSchema).parse(raw)),
  );
  return { books: data.books, nextCursor: data.next_cursor ?? null };
}

/**
 * 详情接口同时承担两个职责：拿元数据，和拿【带签名的播放地址】。
 * play_url 有时效，调用方拿到后应立即使用，不要落库。
 */
export async function getBookDetail(bookId: string): Promise<CpsBookDetail> {
  const detail = await fetchDetail(bookId, DETAIL_BOOK_TYPE);
  if (detail.chapters.length > 0 || DETAIL_BOOK_TYPE === 0) return detail;
  // book_type 传错时上游会返回空章节而不是报错，回退再试一次
  return fetchDetail(bookId, 0);
}

async function fetchDetail(
  bookId: string,
  bookType: number,
): Promise<CpsBookDetail> {
  const endpoint = "/api/v1/book/book-detail";
  return post(
    endpoint,
    { app: APP, book_id: bookId, book_type: bookType },
    (raw) => unwrap(endpoint, envelope(bookDetailSchema).parse(raw)),
  );
}

export interface BillParams {
  minDate: string;
  maxDate: string;
  page?: number;
  limit?: number;
  bookIds?: readonly string[];
}

export interface BillResult {
  list: readonly CpsBillItem[];
  total: number;
}

export async function getBillEstimate(params: BillParams): Promise<BillResult> {
  const endpoint = "/api/v1/bill/app-estimate";
  const data = await post(
    endpoint,
    {
      app: APP,
      book_ids: params.bookIds ?? [],
      min_date: params.minDate,
      max_date: params.maxDate,
      promotion_value: "",
      page: params.page ?? 1,
      limit: params.limit ?? 20,
    },
    (raw) => unwrap(endpoint, envelope(billDataSchema).parse(raw)),
  );
  return { list: data.list, total: data.total };
}

/** 遍历全部分页。上游游标分页，limit 上限 40，大片库同步时用这个。 */
export async function* iterateAllBooks(
  params: Omit<ListBooksParams, "page" | "cursor"> = {},
): AsyncGenerator<readonly CpsBookListItem[]> {
  let cursor: string | null = null;
  let page = 1;
  const seen = new Set<string>();

  while (true) {
    const result: ListBooksResult = await listBooks({
      ...params,
      page,
      cursor: cursor ?? undefined,
    });
    const fresh = result.books.filter((b) => !seen.has(b.id));
    fresh.forEach((b) => seen.add(b.id));

    if (fresh.length === 0) {
      if (result.books.length > 0 || result.nextCursor) {
        throw new Error("CPS 片库分页重复或未收敛，不能视为采集完成");
      }
      return;
    }
    yield fresh;

    // 游标为空说明到底了；上游偶尔不返回 cursor，用页码兜底
    if (!result.nextCursor && result.books.length < MAX_PAGE_SIZE) return;
    cursor = result.nextCursor;
    page += 1;
  }
}
