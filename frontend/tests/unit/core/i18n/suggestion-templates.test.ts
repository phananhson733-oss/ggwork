import { describe, expect, it } from "@rstest/core";

import type { Translations } from "@/core/i18n/locales/types";
import { loadTranslations } from "@/core/i18n/translations";
import { SUGGESTION_TEMPLATE_PLACEHOLDER_PATTERN } from "@/core/suggestions/placeholders";

type Template = Translations["inputBox"]["suggestions"][number];

function templates(t: Translations): Template[] {
  const more = t.inputBox.suggestionsMore.filter(
    (item): item is Template => !("type" in item),
  );
  return [...t.inputBox.suggestions, ...more];
}

// The composer only runs the pick agent: it has the pick tools and nothing
// else, so the templates must not reach for upstream DeerFlow skills or
// promise saving, publishing or a "never posted" verdict.
const FORBIDDEN = [
  "保存",
  "飞书",
  "定时",
  "从未",
  "没发过",
  "播放量",
  "save",
  "feishu",
  "schedule",
  "never posted",
  "web_search",
  "{{",
];

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
        for (const token of tokens) {
          expect(SUGGESTION_TEMPLATE_PLACEHOLDER_PATTERN.test(token)).toBe(
            true,
          );
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
