import assert from "node:assert/strict";

import { test } from "@rstest/core";

import {
  YOUTUBE_LABEL,
  YOUTUBE_RULES,
  youtubeStatus,
} from "@/core/pick-board/platforms";
import { buildBoardRules } from "@/core/pick-board/rules";

import fixture from "./fixtures/rules.json";

const rules = buildBoardRules(fixture, 7);

test("youtubeStatus 按版本规则判：可发 / 禁 / 慎用直接取标签，限剧单看这一行在不在剧单", () => {
  assert.deepEqual(youtubeStatus(rules, "shortmax", false), {
    label: "YouTube 可发",
    blocked: false,
  });
  assert.deepEqual(youtubeStatus(rules, "moboreels", true), {
    label: "禁 YouTube",
    blocked: true,
  });
  assert.deepEqual(youtubeStatus(rules, "touchshort", false), {
    label: "YouTube 慎用",
    blocked: false,
  });
  assert.deepEqual(youtubeStatus(rules, "kalos", true), {
    label: "YouTube 限剧单 · 在剧单",
    blocked: false,
  });
  assert.deepEqual(youtubeStatus(rules, "kalos", false), {
    label: "YouTube 限剧单 · 不在剧单",
    blocked: true,
  });
});

test("标签取版本里的写法；限剧单的前半句也跟着版本走", () => {
  const renamed = buildBoardRules(
    { ...fixture, youtubeLabels: { ...fixture.youtubeLabels, only: "限单" } },
    7,
  );
  assert.equal(youtubeStatus(renamed, "kalos", true).label, "限单 · 在剧单");
});

test("可发 / 慎用 / 禁三种也取版本里的写法，不回落到静态标签", () => {
  const renamed = buildBoardRules(
    {
      ...fixture,
      youtubeLabels: {
        only: "限单",
        ok: "油管可发",
        warn: "油管慎用",
        no: "油管禁发",
      },
    },
    7,
  );
  assert.deepEqual(youtubeStatus(renamed, "shortmax", false), {
    label: "油管可发",
    blocked: false,
  });
  assert.deepEqual(youtubeStatus(renamed, "touchshort", false), {
    label: "油管慎用",
    blocked: false,
  });
  assert.deepEqual(youtubeStatus(renamed, "moboreels", true), {
    label: "油管禁发",
    blocked: true,
  });
});

test("规则里没有这个剧场，或剧场键不认识：规则未知，不拦也不崩", () => {
  const rest = Object.fromEntries(
    Object.entries(fixture.platformRules).filter(([k]) => k !== "touchshort"),
  );
  const partial = buildBoardRules({ ...fixture, platformRules: rest }, 7);
  assert.deepEqual(youtubeStatus(partial, "touchshort", false), {
    label: "规则未知",
    blocked: false,
  });
  assert.deepEqual(youtubeStatus(rules, "netflix", false), {
    label: "规则未知",
    blocked: false,
  });
});

test("静态兜底标签覆盖全部四种 YouTube 规则", () => {
  assert.deepEqual(Object.keys(YOUTUBE_LABEL), [...YOUTUBE_RULES]);
});
