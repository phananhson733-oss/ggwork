import { describe, expect, it } from "@rstest/core";

import type { Translations } from "@/core/i18n/locales/types";
import { loadTranslations } from "@/core/i18n/translations";
import { findSuggestionTemplatePlaceholder } from "@/core/suggestions/placeholders";

type Template = Translations["inputBox"]["suggestions"][number];

function templates(t: Translations): Template[] {
  const more = t.inputBox.suggestionsMore.filter(
    (item): item is Template => !("type" in item),
  );
  return [...t.inputBox.suggestions, ...more];
}

// The composer only runs the pick agent: it has the pick tools and nothing
// else, so the templates must not reach for upstream DeerFlow skills or
// promise saving, publishing or a "never posted" verdict. This is a lexical
// guard on the wording; the per-template pins below carry the tool semantics.
const FORBIDDEN = [
  "保存",
  "飞书",
  "定时",
  "从未",
  "没发过",
  "发布到",
  "发布至",
  "播放量",
  "save",
  "feishu",
  "schedule",
  "never posted",
  "web_search",
  "{{",
];

// Phrases each template must keep, because PICK_INSTRUCTIONS and the tool
// descriptions map them to specific filters (and 《》 marks a user-typed title
// for the answer check). `absent` guards a wrong mapping, e.g. kw has no ranks.
const PINS: Record<
  "en-US" | "zh-CN",
  Record<string, { present: string[]; absent?: string[] }>
> = {
  "en-US": {
    "Find candidates": {
      present: ["(en)", "already picked", "posting records"],
    },
    "By theater": {
      present: ["[theater]", "already picked", "instead of switching"],
    },
    "KalosTV daily": { present: ["(kd)", "by rank", "already picked"] },
    "Exclude by account": {
      present: [
        "[account]",
        "posting records",
        "already picked",
        "instead of excluding everything the team has posted",
      ],
    },
    "KalosTV weekly hot": {
      present: ["(kw)", "already picked"],
      absent: ["rank"],
    },
    "Skip YouTube bans": {
      present: ["explicitly banned", "confirmed eligibility not required"],
    },
    "Look up a drama": {
      present: [
        "《[drama title]》",
        "do not exclude ones I have already picked",
      ],
    },
    "Theater rules": {
      present: ["[theater]", "rule source", "unknown"],
    },
    "Pool count": {
      present: ["How many", "Do not exclude ones I have already picked"],
    },
  },
  "zh-CN": {
    找候选: { present: ["英语剧", "我已经选过的", "发布记录里发过的"] },
    按剧场: {
      present: ["[剧场]", "我已经选过的", "不要换成别的剧场"],
    },
    KalosTV日榜: { present: ["（kd）", "按名次", "我已经选过的"] },
    按账号排除: {
      present: [
        "[账号名]",
        "发布记录里发过的",
        "我已经选过的",
        "不要改成排除团队全部已发的",
      ],
    },
    KalosTV周热门: {
      present: ["（kw）", "依据", "我已经选过的"],
      absent: ["名次"],
    },
    排除YouTube禁用: { present: ["只排除明确禁用", "不要求确认可发"] },
    查一部剧: { present: ["《[剧名]》", "不排除我已经选过的"] },
    剧场规则: { present: ["[剧场]", "规则来源", "写未知"] },
    盘点候选池: { present: ["一共多少部", "不排除我已经选过的"] },
  },
};

describe("pick quick-action templates", () => {
  it("keep the same shape, order and icons in both locales", async () => {
    const [english, chinese] = await Promise.all([
      loadTranslations("en-US"),
      loadTranslations("zh-CN"),
    ]);
    for (const t of [english, chinese]) {
      expect(t.inputBox.suggestions).toHaveLength(4);
      expect(t.inputBox.suggestionsMore).toHaveLength(6);
      expect(
        t.inputBox.suggestionsMore.filter((item) => "type" in item),
      ).toHaveLength(1);
      expect(t.inputBox.suggestionsMoreLabel).toBeTruthy();
    }
    expect(templates(english).map((item) => item.icon)).toEqual(
      templates(chinese).map((item) => item.icon),
    );
    expect(
      english.inputBox.suggestionsMore.map((item) => "type" in item),
    ).toEqual(chinese.inputBox.suggestionsMore.map((item) => "type" in item));
  });

  it("mark every placeholder so the composer selects it and blocks sending", async () => {
    for (const locale of ["en-US", "zh-CN"] as const) {
      const t = await loadTranslations(locale);
      for (const { prompt } of templates(t)) {
        const tokens = prompt.match(/\[[^\]]+\]/g) ?? [];
        expect(tokens.length).toBeLessThanOrEqual(1);
        if (tokens.length === 0) {
          continue;
        }
        // Run the composer's own check on the whole prompt, so the text after
        // the token (which decides the Markdown-link exception) counts too.
        const found = findSuggestionTemplatePlaceholder(prompt);
        expect(found && prompt.slice(found.start, found.end)).toBe(tokens[0]);
      }
    }
  });

  it("keep the phrases that select each template's filters", async () => {
    for (const locale of ["en-US", "zh-CN"] as const) {
      const t = await loadTranslations(locale);
      const byLabel = new Map(
        templates(t).map((item) => [item.suggestion, item.prompt]),
      );
      expect([...byLabel.keys()].sort()).toEqual(
        Object.keys(PINS[locale]).sort(),
      );
      for (const [label, pin] of Object.entries(PINS[locale])) {
        const prompt = byLabel.get(label) ?? "";
        for (const phrase of pin.present) {
          expect(prompt).toContain(phrase);
        }
        for (const phrase of pin.absent ?? []) {
          expect(prompt).not.toContain(phrase);
        }
      }
    }
  });

  it("use unique, short labels and stay within the pick tools", async () => {
    for (const locale of ["en-US", "zh-CN"] as const) {
      const t = await loadTranslations(locale);
      const labels = templates(t).map((item) => item.suggestion);
      expect(new Set(labels).size).toBe(labels.length);
      for (const label of labels) {
        expect(label.length).toBeLessThanOrEqual(locale === "en-US" ? 20 : 12);
      }
      for (const { prompt } of templates(t)) {
        for (const word of FORBIDDEN) {
          expect(prompt.toLowerCase()).not.toContain(word);
        }
      }
    }
  });

  it("no longer ship the upstream Surprise and Create entries", async () => {
    for (const locale of ["en-US", "zh-CN"] as const) {
      const t = await loadTranslations(locale);
      expect("surpriseMe" in t.inputBox).toBe(false);
      expect("suggestionsCreate" in t.inputBox).toBe(false);
    }
  });
});
