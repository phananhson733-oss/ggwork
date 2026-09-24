import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";

import { describe, test } from "@rstest/core";

import { YOUTUBE_LABEL, youtubeStatus } from "@/core/pick-board/platforms";
import {
  BoardRulesInvalid,
  GLOSSARY_ITEM_KEYS,
  PLATFORM_RULE_KEYS,
  RULES_KEYS,
  buildBoardRules,
} from "@/core/pick-board/rules";

import fixture from "./fixtures/rules.json";
import { REPO_ROOT } from "./ported-source";

type Json = Record<string, unknown>;

/** A fresh, mutable copy of the fixture's meta.rules to bend per case. */
function raw(): Json {
  return structuredClone(fixture) as Json;
}

function withRule(patch: Json, key = "kalos"): Json {
  const r = raw();
  const rules = r.platformRules as Record<string, Json>;
  return {
    ...r,
    platformRules: { ...rules, [key]: { ...rules[key], ...patch } },
  };
}

function without(object: Json, key: string): Json {
  return Object.fromEntries(Object.entries(object).filter(([k]) => k !== key));
}

function rejection(input: unknown): BoardRulesInvalid {
  try {
    buildBoardRules(input, 7);
  } catch (error) {
    assert.ok(error instanceof BoardRulesInvalid, String(error));
    return error;
  }
  assert.fail("expected BoardRulesInvalid");
}

describe("the backend contract fixture", () => {
  const backend = JSON.parse(
    readFileSync(
      path.join(
        REPO_ROOT,
        "customizations/pick-workbench/tests/fixtures/export_v2_contract.json",
      ),
      "utf8",
    ),
  ) as { manifest: { meta: { rules: unknown } } };

  test("the frontend copy equals manifest.meta.rules", () => {
    assert.deepEqual(fixture, backend.manifest.meta.rules);
  });

  test("RealShort's real meta.rules passes and builds", () => {
    const rules = buildBoardRules(backend.manifest.meta.rules, 7);
    assert.deepEqual(rules.inUse, [
      "reelshort",
      "dramabox",
      "shortmax",
      "flickreels",
      "flareflow",
    ]);
    assert.deepEqual(rules.unknownPlatforms, []);
    assert.deepEqual(rules.ytBlocked, ["flareflow", "moboreels"]);
    assert.deepEqual(rules.ytListOnly, ["kalos", "starshort"]);
    assert.equal(rules.glossary.length, 3);
  });

  test("the TS schema names the same keys as contracts.py", () => {
    const py = readFileSync(
      path.join(
        REPO_ROOT,
        "customizations/pick-workbench/ggwork_pick/mirror/contracts.py",
      ),
      "utf8",
    );
    const flat = (name: string) => {
      const keys =
        new RegExp(`${name} = _flat\\("${name}", \\(([^)]*)\\)`).exec(
          py,
        )?.[1] ?? "";
      return [...keys.matchAll(/"([^"]+)"/g)].map((m) => m[1]);
    };
    assert.deepEqual([...PLATFORM_RULE_KEYS], flat("PlatformRule"));
    assert.deepEqual([...GLOSSARY_ITEM_KEYS], flat("GlossaryItem"));
    const body =
      /class Rules\(StrictContract\):\n((?: {4}.*\n)+)/.exec(py)?.[1] ?? "";
    const fields = [...body.matchAll(/^ {4}(\w+):/gm)].map((m) => m[1]);
    assert.deepEqual([...RULES_KEYS], fields);
    assert.match(
      body,
      /_keyed\("YoutubeLabels", \("ok", "only", "warn", "no"\)/,
    );
  });
});

describe("links", () => {
  test("an /admin/pick doc becomes a workbench link pinned to the version", () => {
    const rules = buildBoardRules(raw(), 7);
    assert.equal(
      rules.platformRules.reelshort?.doc,
      "/workspace/pick-data?tab=rank&rk=rs_rr&v=7",
    );
  });

  test("a v already in the doc query is replaced, not repeated", () => {
    const rules = buildBoardRules(
      withRule({ doc: "/admin/pick?tab=rank&v=3&rk=kd" }),
      12,
    );
    assert.equal(
      rules.platformRules.kalos?.doc,
      "/workspace/pick-data?tab=rank&rk=kd&v=12",
    );
  });

  test("https links stay; javascript:, http:, protocol-relative and other paths are blanked", () => {
    const keep = "https://example.feishu.cn/wiki/placeholder1";
    assert.equal(
      buildBoardRules(withRule({ doc: keep }), 7).platformRules.kalos?.doc,
      keep,
    );
    for (const doc of [
      "javascript:alert(1)",
      "JAVASCRIPT:alert(1)",
      "http://example.com/x",
      "//evil.example/admin/pick?x=1",
      "/admin/pickx?tab=rank",
      "/admin/users?x=1",
      " https://example.com",
      "HTTPS://example.com",
      "data:text/html,x",
    ])
      assert.equal(
        buildBoardRules(withRule({ doc }), 7).platformRules.kalos?.doc,
        "",
        doc,
      );
  });

  test("postedPoolUrl follows the same rule", () => {
    assert.equal(
      buildBoardRules({ ...raw(), postedPoolUrl: "javascript:void(0)" }, 7)
        .postedPoolUrl,
      "",
    );
    assert.equal(
      buildBoardRules({ ...raw(), postedPoolUrl: "/admin/pick?tab=posted" }, 4)
        .postedPoolUrl,
      "/workspace/pick-data?tab=posted&v=4",
    );
    assert.equal(
      buildBoardRules(raw(), 7).postedPoolUrl,
      "https://example.feishu.cn/base/placeholder10",
    );
  });

  test("a doc that is a number, or null, is blanked (Python accepts any scalar)", () => {
    assert.equal(
      buildBoardRules(withRule({ doc: 42 }), 7).platformRules.kalos?.doc,
      "",
    );
    assert.equal(
      buildBoardRules(withRule({ doc: null }), 7).platformRules.kalos?.doc,
      "",
    );
    assert.equal(
      buildBoardRules({ ...raw(), postedPoolUrl: 3 }, 7).postedPoolUrl,
      "",
    );
  });
});

describe("labels and derived lists", () => {
  test("rs_ledger is always 订单对账, whatever the version says", () => {
    const r = raw();
    const labels = {
      ...(r.rsRankLabels as Json),
      rs_ledger: "ReelShort 分成对账",
    };
    const rules = buildBoardRules({ ...r, rsRankLabels: labels }, 7);
    assert.equal(rules.rsRankLabels.rs_ledger, "ReelShort 订单对账");
  });

  test("youtubeLabels may lack keys: the missing ones fall back to the static labels", () => {
    const rules = buildBoardRules(
      { ...raw(), youtubeLabels: { ok: "可发（版本）" } },
      7,
    );
    assert.deepEqual(rules.youtubeLabels, {
      ...YOUTUBE_LABEL,
      ok: "可发（版本）",
    });
    assert.deepEqual(
      buildBoardRules({ ...raw(), youtubeLabels: {} }, 7).youtubeLabels,
      YOUTUBE_LABEL,
    );
  });

  test("yt=null (or any value outside the enum) is 规则未知: not blocked, not list-only", () => {
    for (const yt of [null, "maybe", 1, true]) {
      const rules = buildBoardRules(withRule({ yt }, "moboreels"), 7);
      assert.equal(rules.platformRules.moboreels?.yt, null, String(yt));
      assert.ok(!rules.ytBlocked.includes("moboreels"));
      assert.ok(!rules.ytListOnly.includes("moboreels"));
      assert.deepEqual(youtubeStatus(rules, "moboreels", false), {
        label: "规则未知",
        blocked: false,
      });
    }
  });

  test("turning a theater's yt from ok to no moves it into ytBlocked", () => {
    const before = buildBoardRules(raw(), 7);
    assert.ok(!before.ytBlocked.includes("shortmax"));
    const after = buildBoardRules(withRule({ yt: "no" }, "shortmax"), 7);
    assert.deepEqual(after.ytBlocked, ["shortmax", "flareflow", "moboreels"]);
    assert.equal(youtubeStatus(after, "shortmax", true).blocked, true);
  });

  test("platform keys outside the static list are unknownPlatforms, kept in the rules and the yt lists", () => {
    const r = raw();
    const rules = r.platformRules as Record<string, Json>;
    const next = {
      ...rules,
      zeta: { ...rules.moboreels, key: "zeta", name: "Zeta", yt: "no" },
    };
    const built = buildBoardRules({ ...r, platformRules: next }, 7);
    assert.deepEqual(built.unknownPlatforms, ["zeta"]);
    assert.equal(built.platformRules.zeta?.name, "Zeta");
    assert.deepEqual(built.ytBlocked, ["flareflow", "moboreels", "zeta"]);
  });

  test("a label map missing a key falls back to the static label; extra keys are dropped", () => {
    const r = raw();
    const basis = without(r.basisLabels as Json, "kd");
    const built = buildBoardRules(
      { ...r, basisLabels: { ...basis, zz: "新依据" } },
      7,
    );
    assert.equal(built.basisLabels.kd, "KalosTV 日榜");
    assert.ok(!("zz" in built.basisLabels));
    assert.equal(
      built.basisDateLabels.sm,
      "",
      "an empty date label stays empty",
    );
  });

  test("scalars are shown as text: numbers become strings, null and booleans become empty", () => {
    const built = buildBoardRules(
      withRule({ name: 5, updated: null, tag: true }),
      7,
    );
    assert.equal(built.platformRules.kalos?.name, "5");
    assert.equal(built.platformRules.kalos?.updated, "");
    assert.equal(built.platformRules.kalos?.tag, "");
    assert.deepEqual(
      buildBoardRules({ ...raw(), inUse: ["reelshort", 3, null, "kalos"] }, 7)
        .inUse,
      ["reelshort", "kalos"],
    );
  });

  test("glossary items keep only what the page reads: ask is false or absent", () => {
    const r = raw();
    const glossary = [
      {
        g: "组",
        d: 1,
        items: [
          { t: "词", ask: true, d: "说明", a: null, h: 2 },
          { d: "只有正文", ask: false },
        ],
      },
    ];
    const built = buildBoardRules({ ...r, glossary }, 7);
    assert.deepEqual(built.glossary, [
      {
        g: "组",
        d: "1",
        items: [
          { t: "词", d: "说明", h: "2" },
          { t: "", d: "只有正文", ask: false },
        ],
      },
    ]);
  });
});

describe("the result", () => {
  test("is deeply frozen and leaves the input untouched", () => {
    const input = raw();
    const snapshot = structuredClone(input);
    const rules = buildBoardRules(input, 7);
    assert.deepEqual(input, snapshot);
    assert.ok(Object.isFrozen(rules));
    assert.ok(Object.isFrozen(rules.platformRules));
    assert.ok(Object.isFrozen(rules.platformRules.kalos));
    assert.ok(Object.isFrozen(rules.glossary[0]?.items[0]));
    assert.ok(Object.isFrozen(rules.ytBlocked));
  });

  test("a version id outside 1..999999 is a caller bug", () => {
    for (const id of [0, -1, 1_000_000, 1.5, Number.NaN])
      assert.throws(() => buildBoardRules(raw(), id), RangeError, String(id));
  });
});

describe("validation is as wide as contracts.py and fails with a typed error", () => {
  test("what Python refuses, TS refuses: extra keys, missing keys, nested values", () => {
    const r = raw();
    const cases: [unknown, string][] = [
      [null, "(root)"],
      [{ ...r, extra: 1 }, "(root)"],
      [without(r, "glossary"), "glossary"],
      [withRule({ yt: ["no"] }), "platformRules.kalos.yt"],
      [withRule({ extra: "x" }), "platformRules.kalos"],
      [{ ...r, youtubeLabels: { ok: "x", maybe: "y" } }, "youtubeLabels"],
      [{ ...r, inUse: "reelshort" }, "inUse"],
      [{ ...r, sortLabels: { rr: { nested: 1 } } }, "sortLabels.rr"],
    ];
    for (const [input, where] of cases) {
      const error = rejection(input);
      assert.equal(error.code, "board_rules_invalid");
      assert.ok(
        error.paths.includes(where),
        `${where} not in ${error.paths.join(", ")}`,
      );
    }
  });

  test("the error names places, never values or odd keys", () => {
    const r = raw();
    const hints = {
      ...(r.ruleHints as Json),
      "密码 hunter2": { v: "secret-value" },
    };
    const error = rejection({
      ...r,
      ruleHints: hints,
      postedPoolUrl: { token: "tok-123" },
    });
    assert.ok(error.paths.includes("ruleHints.<key>"));
    assert.ok(error.paths.includes("postedPoolUrl"));
    for (const leaked of ["hunter2", "secret-value", "tok-123", "密码"])
      assert.ok(
        !error.message.includes(leaked) && !error.paths.join().includes(leaked),
        leaked,
      );
  });
});
