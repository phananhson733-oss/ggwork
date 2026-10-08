/**
 * CPS 接口的 lang 字段返回的是中文语言名（"英语" / "波兰语"），
 * 而站点路由和 hreflang 需要 BCP 47 代码。这里做双向映射。
 *
 * 新语言上线时只需在 LANGUAGE_TABLE 追加一行；未识别的语言会被 sync 跳过并记日志，
 * 不会写进库——避免出现 /undefined/ 这类路由。
 */

export interface LocaleMeta {
  /** URL 与 hreflang 使用的代码 */
  code: string;
  /** 接口返回的中文名 */
  cnName: string;
  /** 语言自称，用于语言切换器 */
  nativeName: string;
  /** hreflang 属性值，通常等于 code */
  hreflang: string;
  dir: "ltr" | "rtl";
}

const LANGUAGE_TABLE: readonly LocaleMeta[] = [
  {
    code: "en",
    cnName: "英语",
    nativeName: "English",
    hreflang: "en",
    dir: "ltr",
  },
  {
    code: "es",
    cnName: "西班牙语",
    nativeName: "Español",
    hreflang: "es",
    dir: "ltr",
  },
  {
    code: "pt",
    cnName: "葡萄牙语",
    nativeName: "Português",
    hreflang: "pt",
    dir: "ltr",
  },
  {
    code: "de",
    cnName: "德语",
    nativeName: "Deutsch",
    hreflang: "de",
    dir: "ltr",
  },
  {
    code: "fr",
    cnName: "法语",
    nativeName: "Français",
    hreflang: "fr",
    dir: "ltr",
  },
  {
    code: "it",
    cnName: "意大利语",
    nativeName: "Italiano",
    hreflang: "it",
    dir: "ltr",
  },
  {
    code: "pl",
    cnName: "波兰语",
    nativeName: "Polski",
    hreflang: "pl",
    dir: "ltr",
  },
  {
    code: "id",
    cnName: "印尼语",
    nativeName: "Bahasa Indonesia",
    hreflang: "id",
    dir: "ltr",
  },
  { code: "th", cnName: "泰语", nativeName: "ไทย", hreflang: "th", dir: "ltr" },
  {
    code: "vi",
    cnName: "越南语",
    nativeName: "Tiếng Việt",
    hreflang: "vi",
    dir: "ltr",
  },
  {
    code: "ja",
    cnName: "日语",
    nativeName: "日本語",
    hreflang: "ja",
    dir: "ltr",
  },
  {
    code: "ko",
    cnName: "韩语",
    nativeName: "한국어",
    hreflang: "ko",
    dir: "ltr",
  },
  {
    code: "tr",
    cnName: "土耳其语",
    nativeName: "Türkçe",
    hreflang: "tr",
    dir: "ltr",
  },
  {
    code: "ar",
    cnName: "阿拉伯语",
    nativeName: "العربية",
    hreflang: "ar",
    dir: "rtl",
  },
  {
    code: "ru",
    cnName: "俄语",
    nativeName: "Русский",
    hreflang: "ru",
    dir: "ltr",
  },
  {
    code: "hi",
    cnName: "印地语",
    nativeName: "हिन्दी",
    hreflang: "hi",
    dir: "ltr",
  },
  {
    code: "ms",
    cnName: "马来语",
    nativeName: "Bahasa Melayu",
    hreflang: "ms",
    dir: "ltr",
  },
  {
    code: "tl",
    cnName: "菲律宾语",
    nativeName: "Filipino",
    hreflang: "tl",
    dir: "ltr",
  },
  {
    code: "zh",
    cnName: "中文",
    nativeName: "中文",
    hreflang: "zh",
    dir: "ltr",
  },
  // 以下 4 个由 scripts/probe-lang.ts 实测补入：曾占片库 5,445 部（约 18%）被静默跳过。
  // code 一律小写（BY_CODE 按小写查），hreflang 用 BCP 47 规范大小写。
  {
    code: "zh-hant",
    cnName: "繁体中文",
    nativeName: "繁體中文",
    hreflang: "zh-Hant",
    dir: "ltr",
  },
  {
    code: "ro",
    cnName: "罗马尼亚语",
    nativeName: "Română",
    hreflang: "ro",
    dir: "ltr",
  },
  {
    code: "bg",
    cnName: "保加利亚语",
    nativeName: "Български",
    hreflang: "bg",
    dir: "ltr",
  },
  {
    code: "cs",
    cnName: "捷克语",
    nativeName: "Čeština",
    hreflang: "cs",
    dir: "ltr",
  },
] as const;

const BY_CN_NAME = new Map(LANGUAGE_TABLE.map((l) => [l.cnName, l]));
const BY_CODE = new Map(LANGUAGE_TABLE.map((l) => [l.code, l]));

/** 接口里出现过的别名，统一归并到主条目 */
const CN_NAME_ALIASES: Readonly<Record<string, string>> = {
  英文: "英语",
  中文简体: "中文",
  简体中文: "中文",
  葡萄牙语巴西: "葡萄牙语",
  西班牙文: "西班牙语",
  菲律宾他加禄语: "菲律宾语",
};

export const ALL_LOCALES: readonly LocaleMeta[] = LANGUAGE_TABLE;
export const LOCALE_CODES: readonly string[] = LANGUAGE_TABLE.map(
  (l) => l.code,
);
export const DEFAULT_LOCALE = "en";

/** 中文语言名 → locale。识别不了返回 null，由调用方决定跳过还是兜底。 */
export function localeFromCnName(cnName: string): LocaleMeta | null {
  const trimmed = cnName.trim();
  if (!trimmed) return null;
  const canonical = CN_NAME_ALIASES[trimmed] ?? trimmed;
  return BY_CN_NAME.get(canonical) ?? null;
}

export function localeFromCode(code: string): LocaleMeta | null {
  return BY_CODE.get(code.toLowerCase()) ?? null;
}

export function isSupportedLocale(code: string): boolean {
  return BY_CODE.has(code.toLowerCase());
}

export function localeDir(code: string): "ltr" | "rtl" {
  return BY_CODE.get(code.toLowerCase())?.dir ?? "ltr";
}
