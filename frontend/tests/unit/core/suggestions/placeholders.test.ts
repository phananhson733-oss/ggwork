import { describe, expect, test } from "@rstest/core";

import { findSuggestionTemplatePlaceholder } from "@/core/suggestions/placeholders";

function selected(text: string): string | null {
  const found = findSuggestionTemplatePlaceholder(text);
  return found ? text.slice(found.start, found.end) : null;
}

describe("findSuggestionTemplatePlaceholder", () => {
  test("finds Chinese [剧场] and returns correct range", () => {
    const result = findSuggestionTemplatePlaceholder(
      "找5部[剧场]的英语剧，排除我已经选过的",
    );
    expect(result).toEqual({ start: 3, end: 7 });
  });

  test("finds English [theater] and returns correct range", () => {
    const result = findSuggestionTemplatePlaceholder(
      "Find 5 English (en) dramas from [theater], excluding ones I have already picked",
    );
    expect(result).toEqual({ start: 32, end: 41 });
  });

  test("finds the account placeholders", () => {
    expect(selected("找5部英语剧，排除账号[账号名]在发布记录里发过的")).toBe(
      "[账号名]",
    );
    expect(selected("excluding ones account [account] has posted")).toBe(
      "[account]",
    );
  });

  test("selects only the title placeholder inside 《》", () => {
    expect(selected("在候选池里查《[剧名]》，列出依据和发布记录")).toBe(
      "[剧名]",
    );
    expect(selected("Look up 《[drama title]》 in the candidate pool")).toBe(
      "[drama title]",
    );
  });

  test("no longer treats the retired upstream tokens as placeholders", () => {
    expect(findSuggestionTemplatePlaceholder("研究一下[主题]")).toBeNull();
    expect(findSuggestionTemplatePlaceholder("Research [topic]")).toBeNull();
    expect(findSuggestionTemplatePlaceholder("从[来源]收集数据")).toBeNull();
    expect(findSuggestionTemplatePlaceholder("Collect [source]")).toBeNull();
  });

  test("ignores Markdown link text that happens to match a token", () => {
    expect(
      findSuggestionTemplatePlaceholder("see [account](https://example.com)"),
    ).toBeNull();
    expect(
      findSuggestionTemplatePlaceholder("[drama title](https://example.com)"),
    ).toBeNull();
  });

  test("returns null for normal text without brackets", () => {
    expect(
      findSuggestionTemplatePlaceholder("找5部英语剧，排除我已经选过的"),
    ).toBeNull();
  });

  test("returns null for text with unrelated brackets", () => {
    expect(
      findSuggestionTemplatePlaceholder("check [this link] for details"),
    ).toBeNull();
  });

  test("returns null for empty text", () => {
    expect(findSuggestionTemplatePlaceholder("")).toBeNull();
  });

  test("detects placeholder case-insensitively for English", () => {
    expect(selected("from [Theater] only")).toBe("[Theater]");
    expect(selected("from [THEATER] only")).toBe("[THEATER]");
  });
});
