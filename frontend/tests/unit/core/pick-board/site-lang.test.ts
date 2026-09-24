import assert from "node:assert/strict";

import { test } from "@rstest/core";

import { LANG_LOC } from "@/core/pick-board/catalog-lang";
import { LOCALE_CODES, localeFromCode } from "@/core/pick-board/lang";
import {
  REALSHORT_ROW_URL,
  dramaPath,
  realshortRowUrl,
} from "@/core/pick-board/site";

test("dramaPath 是 ReelShort 公开站的绝对地址，slug 只编码一段", () => {
  assert.equal(
    dramaPath("en", "The Dragon's Contract"),
    "https://dramashortstv.com/en/drama/The%20Dragon's%20Contract",
  );
  assert.equal(
    dramaPath("ru", "дракон/2"),
    "https://dramashortstv.com/ru/drama/%D0%B4%D1%80%D0%B0%D0%BA%D0%BE%D0%BD%2F2",
  );
  assert.ok(
    dramaPath("../../x", "a").startsWith("https://dramashortstv.com/"),
    "坏 locale 出不了这个站",
  );
});

test("RealShort 证据页链接与 feed-map.ts 的 rowRef 同形", () => {
  assert.equal(
    REALSHORT_ROW_URL,
    "https://dramashortstv.com/admin/pick?tab=row&row=",
  );
  assert.equal(
    realshortRowUrl("goodshort-K10JEicNmxOWhQPwdg3zdw=="),
    "https://dramashortstv.com/admin/pick?tab=row&row=goodshort-K10JEicNmxOWhQPwdg3zdw%3D%3D",
  );
});

test("语种码表：小写 code 查得到中文名，大小写不敏感，查不到回 null", () => {
  assert.ok(LOCALE_CODES.includes("en") && LOCALE_CODES.includes("zh-hant"));
  assert.equal(localeFromCode("ZH-HANT")?.cnName, "繁体中文");
  assert.equal(localeFromCode("xx"), null);
  for (const code of LOCALE_CODES) assert.equal(code, code.toLowerCase());
});

test("剧单语种兜底表的每个 locale 都在语种码表里", () => {
  for (const [cn, loc] of Object.entries(LANG_LOC))
    assert.ok(localeFromCode(loc), `${cn} → ${loc} 不在 LOCALE_CODES`);
});
