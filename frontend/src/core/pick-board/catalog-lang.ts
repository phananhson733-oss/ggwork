// PORTED_FROM: realshort@816ca2e src/lib/pick/catalog-import.ts
// 本地改动：只取 RawPost 类型（:82，发布记录 posts 的形状）与 LANG_LOC（:261）。LANG_LOC 只作兜底，
// 运行时以版本数据的 rules.langLoc 为准。
export interface RawPost {
  d?: string | null;
  acct?: string;
  st?: string;
  views?: number | null;
  likes?: number | null;
  favs?: number | null;
  cmts?: number | null;
  shares?: number | null;
  /** 指标日期（播放等数字是哪天回填的） */
  md?: string | null;
  url?: string;
  note?: string | null;
  pid?: string;
}

/** 剧单里的语种中文名 → 本站 locale。与 render.py 的 LANG_LOC 逐条相同 */
export const LANG_LOC: Record<string, string> = {
  英语: "en",
  西班牙语: "es",
  日语: "ja",
  葡萄牙语: "pt",
  印尼语: "id",
  泰语: "th",
  繁体中文: "zh-hant",
  简体中文: "zh",
  法语: "fr",
  韩语: "ko",
  德语: "de",
  阿拉伯语: "ar",
  意大利语: "it",
  越南语: "vi",
  俄语: "ru",
  土耳其语: "tr",
  菲律宾语: "tl",
  马来语: "ms",
  波兰语: "pl",
  印地语: "hi",
  保加利亚语: "bg",
  捷克语: "cs",
  罗马尼亚语: "ro",
};
