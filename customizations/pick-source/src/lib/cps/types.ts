import { z } from "zod";

/**
 * CPS 接口返回的字段类型不稳定：部分字段在列表接口里缺省、在详情接口里才有，
 * 空值有时是 null 有时是 ""。这里一律走宽松策略（looseObject + catch），
 * 目的是"永远解析得出结果"而不是"严格校验"——上游多给字段不该让页面挂掉。
 */

const looseInt = z.coerce.number().int().catch(0);
const looseNum = z.coerce.number().catch(0);
const looseStr = z.string().catch("");

/** 接口统一信封 */
export function envelope<T extends z.ZodType>(data: T) {
  return z.object({
    code: z.number(),
    msg: z.string().optional().default(""),
    data: data.nullable().optional(),
    service_time: z.number().optional(),
  });
}

export const userInfoSchema = z.looseObject({
  user_id: looseInt,
  account: looseStr,
  invitation_code: looseStr,
  distribution_level: looseInt,
  reel_short_promotion_ratio: looseNum,
  kiss_promotion_ratio: looseNum,
  bill_period_type: looseStr,
  is_frozen: looseInt,
  status: looseInt,
});

export const loginDataSchema = z.looseObject({
  accessToken: z.string().min(1),
  user_info: userInfoSchema.optional(),
});

/** 列表项。注意列表接口的 chapter_count / pay_start 常为 0，真值要到详情接口才有。 */
function metricFields(raw: unknown) {
  if (!raw || typeof raw !== "object") return raw;
  const value = raw as Record<string, unknown>;
  const valid = (v: unknown) => (typeof v === "number" || (typeof v === "string" && v.trim() !== "")) && Number.isFinite(Number(v)) && Number(v) >= 0;
  return { ...value, metrics_valid: valid(value.recent_revenue) && valid(value.promoters_cnt) && Number.isInteger(Number(value.promoters_cnt)) };
}

export const bookListItemSchema = z.preprocess(metricFields, z.looseObject({
  id: z.string().min(1),
  title: looseStr,
  desc: looseStr,
  pic: looseStr,
  lang: looseStr,
  book_type: looseInt,
  is_dub: looseInt,
  is_valid: z.coerce.boolean().catch(false),
  pay_start: looseInt,
  chapter_count: looseInt,
  promoters_cnt: looseInt,
  recent_revenue: looseNum,
  publish_at: looseInt,
  tag: z.array(z.string()).catch([]),
  show_tag: z.array(z.string()).catch([]),
  metrics_valid: z.boolean().optional(),
}));

export const allBookDataSchema = z.looseObject({
  books: z.array(bookListItemSchema),
  next_cursor: z.string().nullable().optional(),
});

export const chapterSchema = z.looseObject({
  chapter_id: z.string().min(1),
  /** 集序号，形如 "0001"。缺省时由调用方按数组下标补。 */
  t_chapter_id: looseStr,
  content: looseStr,
  /** 带签名的直链，有时效，禁止落库 */
  play_url: looseStr,
  video_pic: looseStr,
  /** 官方嵌入播放器地址，已内置 cps_id 与 code */
  iframe_src: looseStr,
});

export const relationBookSchema = z.looseObject({
  id: z.string().min(1),
  title: looseStr,
  lang: looseStr,
  pic: looseStr,
  desc: looseStr,
  chapter_count: looseInt,
  publish_at: looseInt,
  pay_start: looseInt,
  book_type: looseInt,
  is_valid: z.coerce.boolean().catch(false),
  is_dub: looseInt,
});

export const bookDetailSchema = z.looseObject({
  id: z.string().min(1),
  title: looseStr,
  desc: looseStr,
  pic: looseStr,
  lang: looseStr,
  book_type: looseInt,
  is_dub: looseInt,
  is_valid: z.coerce.boolean().catch(false),
  pay_start: looseInt,
  chapter_count: looseInt,
  promoters_cnt: looseInt,
  recent_revenue: looseNum,
  publish_at: looseInt,
  tag: z.array(z.string()).catch([]),
  show_tag: z.array(z.string()).catch([]),
  chapters: z.array(chapterSchema).catch([]),
  relation_books: z.array(relationBookSchema).catch([]),
  app_promotion_link: looseStr,
  book_promotion_link: looseStr,
  promotion_code: z.union([z.number(), z.string()]).catch(0),
});

export const billItemSchema = z.looseObject({
  date: looseStr,
  book_title: looseStr,
  book_id: looseStr,
  promotion_type: looseStr,
  promotion_value: looseStr,
  order_cnt: z.union([z.number(), z.string().regex(/^\d+$/)]).transform(Number).pipe(z.number().int().nonnegative()),
  /** 账号预估分成；2026-09-08 与上游收益页核对为 USD，非已结算金额。 */
  total_revenue: z.union([z.number(), z.string().regex(/^-?\d+(\.\d+)?$/)]).transform(Number).pipe(z.number().finite()),
});

export const billDataSchema = z.looseObject({
  list: z.array(billItemSchema),
  total: z.union([z.number(), z.string().regex(/^\d+$/)]).transform(Number).pipe(z.number().int().nonnegative()),
});

export type CpsUserInfo = z.infer<typeof userInfoSchema>;
export type CpsBookListItem = z.infer<typeof bookListItemSchema>;
export type CpsChapter = z.infer<typeof chapterSchema>;
export type CpsRelationBook = z.infer<typeof relationBookSchema>;
export type CpsBookDetail = z.infer<typeof bookDetailSchema>;
export type CpsBillItem = z.infer<typeof billItemSchema>;

/** 上游返回 code !== 0 时抛出，携带原始 code 便于区分"token 过期"与"参数错误" */
export class CpsApiError extends Error {
  constructor(
    readonly endpoint: string,
    readonly code: number,
    message: string,
  ) {
    super(`[CPS ${endpoint}] code=${code} ${message}`);
    this.name = "CpsApiError";
  }
}
