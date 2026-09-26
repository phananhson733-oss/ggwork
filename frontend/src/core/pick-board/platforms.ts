// PORTED_FROM: realshort@816ca2e src/lib/pick/platforms.ts
// 本地改动：十个剧场的规则表（PLATFORM_RULES）不在前端留一份，按版本从 meta.rules.platformRules 构造（rules.ts）；
// YOUTUBE_LABEL 只作版本缺标签时的兜底；youtubeStatus 多收一个 rules，规则缺失或 yt 不认识时是「规则未知」、不拦，
// 另回传规则种类 kind（规则未知时不带），格子按它选语义色。
// POSTED_POOL_URL 不搬：它是运营飞书表的业务链接，不进这个仓库，页面用版本里的 rules.postedPoolUrl。
import type { BoardRules } from "./rules";

export const YOUTUBE_RULES = ["ok", "only", "warn", "no"] as const;
export type YoutubeRule = (typeof YOUTUBE_RULES)[number];

/** 静态兜底：版本的 youtubeLabels 缺哪个键就用这里的写法（与 RealShort 816ca2e 相同） */
export const YOUTUBE_LABEL: Readonly<Record<YoutubeRule, string>> = {
  ok: "YouTube 可发",
  only: "YouTube 限剧单",
  warn: "YouTube 慎用",
  no: "禁 YouTube",
};

export const YOUTUBE_UNKNOWN_LABEL = "规则未知";

export function isYoutubeRule(value: unknown): value is YoutubeRule {
  return (YOUTUBE_RULES as readonly unknown[]).includes(value);
}

/**
 * 一个剧场的规则，按版本数据归一后的形状。字段与 RealShort 的 PlatformRule 相同，
 * 只是 key 是任意剧场键（版本里可能出现本地不认识的剧场），yt 为 null 表示规则未知。
 */
export interface PlatformRule {
  key: string;
  name: string;
  /** 站内链接已改写成 /workspace/pick-data?…&v=；外链只留 https://；其余是空串 */
  doc: string;
  /** 剧场文档最后核对的日期 */
  updated: string;
  /** 结算 */
  back: string;
  /** 报备 */
  report: string;
  yt: YoutubeRule | null;
  ytNote: string;
  /** 必带 tag */
  tag: string;
  /** 解禁通道 */
  unban: string;
  /** 素材从哪拿 */
  material: string;
  /** 这个剧场给了哪些榜单 / 评级信号 */
  signals: string;
}

/**
 * 一行的 YouTube 判定。kind 是剧场的规则（格子据此选语义色），规则未知时没有 kind；
 * blocked 是这一行发不了：禁 YouTube，或限剧单而这一行不在剧单上。
 */
export interface YoutubeStatus {
  label: string;
  blocked: boolean;
  kind?: YoutubeRule;
}

/** 这一行在 YouTube 上能不能发：限剧单的剧场还要看这一行自己在不在 YouTube 剧单上 */
export function youtubeStatus(
  rules: Pick<BoardRules, "platformRules" | "youtubeLabels">,
  platform: string,
  rowOnList: boolean,
): YoutubeStatus {
  const yt = rules.platformRules[platform]?.yt ?? null;
  if (yt === null) return { label: YOUTUBE_UNKNOWN_LABEL, blocked: false };
  const label = rules.youtubeLabels[yt];
  if (yt === "only")
    return rowOnList
      ? { label: `${label} · 在剧单`, blocked: false, kind: yt }
      : { label: `${label} · 不在剧单`, blocked: true, kind: yt };
  return { label, blocked: yt === "no", kind: yt };
}
