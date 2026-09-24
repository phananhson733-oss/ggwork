/**
 * Source contracts of the pick data board (P3-4, with critique C30): what
 * the three pick-board folders may name, import and link to. Checked on the
 * TypeScript syntax tree, so comments never count and JSX is read as JSX.
 *
 * - No pan columns, no money fields, no Money / Usd helpers anywhere.
 * - Only src/server/pick-board talks to pg or drizzle-orm.
 * - Every next/link <Link> passes prefetch={false}.
 * - Components import from @/server only with a whole-statement `import type`
 *   of the barrel (an inline `{ type X }` leaves an empty import behind).
 * - Color utilities and var(--…) all resolve to the pick-board palette.
 * - No relative /admin/pick link, no wall-clock `new Date()`, no static rule
 *   tables in components or queries.
 */
import { readFileSync, readdirSync, statSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "@rstest/core";
import ts from "typescript";

const FRONTEND_ROOT = path.resolve(__dirname, "../../../..");
const CORE_DIR = "src/core/pick-board";
const SERVER_DIR = "src/server/pick-board";
const COMPONENTS_DIR = "src/components/workspace/pick-board";
const BOARD_DIRS = [CORE_DIR, SERVER_DIR, COMPONENTS_DIR] as const;
const THEME_CSS = "src/styles/pick-board-theme.css";
const BOARD_CSS = "src/app/workspace/pick-data/pick-board.css";

function read(relativePath: string): string {
  return readFileSync(path.join(FRONTEND_ROOT, relativePath), "utf8");
}

function walk(relativeDir: string): string[] {
  const absolute = path.join(FRONTEND_ROOT, relativeDir);
  return readdirSync(absolute).flatMap((name) => {
    const child = `${relativeDir}/${name}`;
    if (statSync(path.join(absolute, name)).isDirectory()) return walk(child);
    return /\.(ts|tsx)$/.test(name) ? [child] : [];
  });
}

const parsed = new Map<string, ts.SourceFile>();

function sourceFile(relativePath: string): ts.SourceFile {
  const cached = parsed.get(relativePath);
  if (cached) return cached;
  const kind = relativePath.endsWith(".tsx")
    ? ts.ScriptKind.TSX
    : ts.ScriptKind.TS;
  const file = ts.createSourceFile(
    relativePath,
    read(relativePath),
    ts.ScriptTarget.Latest,
    true,
    kind,
  );
  parsed.set(relativePath, file);
  return file;
}

function nodesOf(file: ts.SourceFile): ts.Node[] {
  const out: ts.Node[] = [];
  const visit = (node: ts.Node) => {
    out.push(node);
    ts.forEachChild(node, visit);
  };
  visit(file);
  return out;
}

/** What REALSHORT_ORIGIN stands for inside a template (core/pick-board/site.ts). */
const KNOWN_TEMPLATE_VALUES: Readonly<Record<string, string>> = {
  REALSHORT_ORIGIN: "https://dramashortstv.com",
};

/** A template's text with known constants filled in and other holes blank. */
function templateText(node: ts.TemplateExpression): string {
  return node.templateSpans.reduce((text, span) => {
    const hole = ts.isIdentifier(span.expression)
      ? (KNOWN_TEMPLATE_VALUES[span.expression.text] ?? "")
      : "";
    return text + hole + span.literal.text;
  }, node.head.text);
}

/** Every piece of text the code carries: literals, templates and JSX text. */
function textsOf(file: ts.SourceFile): string[] {
  return nodesOf(file).flatMap((node) => {
    if (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node))
      return [node.text];
    if (ts.isTemplateExpression(node)) return [templateText(node)];
    if (ts.isJsxText(node)) return [node.text];
    return [];
  });
}

function identifiersOf(file: ts.SourceFile): string[] {
  return nodesOf(file).flatMap((node) =>
    ts.isIdentifier(node) || ts.isPrivateIdentifier(node) ? [node.text] : [],
  );
}

function moduleSpecifiers(
  file: ts.SourceFile,
): { spec: string; node: ts.Node }[] {
  return nodesOf(file).flatMap((node): { spec: string; node: ts.Node }[] => {
    if (
      (ts.isImportDeclaration(node) || ts.isExportDeclaration(node)) &&
      node.moduleSpecifier &&
      ts.isStringLiteral(node.moduleSpecifier)
    )
      return [{ spec: node.moduleSpecifier.text, node }];
    if (
      ts.isCallExpression(node) &&
      (node.expression.kind === ts.SyntaxKind.ImportKeyword ||
        (ts.isIdentifier(node.expression) &&
          node.expression.text === "require")) &&
      node.arguments[0] &&
      ts.isStringLiteral(node.arguments[0])
    )
      return [{ spec: node.arguments[0].text, node }];
    return [];
  });
}

const boardFiles = () => BOARD_DIRS.flatMap((dir) => walk(dir));
const componentFiles = () => walk(COMPONENTS_DIR);

/** Pan columns, money fields and the helpers that printed money. */
const BANNED =
  /pan_url|panUrl|pan_pw|panPw|billUsd|bill_usd|revenue_usd|revenueUsd|promotion_value|promotionValue|\bMoney\b|\bUsd\b|\busd_\d/;

describe("pick-board names", () => {
  it("never names a pan column, a money field or a money helper", () => {
    const offenders = boardFiles().flatMap((file) => {
      const sf = sourceFile(file);
      return [...identifiersOf(sf), ...textsOf(sf)]
        .filter((token) => BANNED.test(token))
        .map((token) => `${file}: ${token}`);
    });
    expect(offenders).toEqual([]);
  });

  it("components and queries never read the static rule tables", () => {
    const statics = new Set([
      "IN_USE",
      "PLATFORM_RULES",
      "GLOSSARY",
      "RULE_HINTS",
      "YOUTUBE_LABEL",
    ]);
    const offenders = [...walk(COMPONENTS_DIR), ...walk(SERVER_DIR)].flatMap(
      (file) =>
        identifiersOf(sourceFile(file))
          .filter((name) => statics.has(name))
          .map((name) => `${file}: ${name}`),
    );
    expect(offenders).toEqual([]);
  });
});

describe("database drivers", () => {
  it("only src/server/pick-board imports pg or drizzle-orm", () => {
    const driver = /^(pg|drizzle-orm)(\/|$)/;
    const quoted = /["'](pg|drizzle-orm)(\/[^"']*)?["']/;
    const offenders = walk("src")
      .filter((file) => quoted.test(read(file)))
      .filter((file) =>
        moduleSpecifiers(sourceFile(file)).some(({ spec }) =>
          driver.test(spec),
        ),
      )
      .filter((file) => !file.startsWith(`${SERVER_DIR}/`));
    expect(offenders).toEqual([]);
  });
});

function linkLocalName(file: ts.SourceFile): string | null {
  for (const statement of file.statements) {
    if (
      ts.isImportDeclaration(statement) &&
      ts.isStringLiteral(statement.moduleSpecifier) &&
      statement.moduleSpecifier.text === "next/link"
    )
      return statement.importClause?.name?.text ?? null;
  }
  return null;
}

function hasPrefetchFalse(attributes: ts.JsxAttributes): boolean {
  const props = attributes.properties;
  if (props.some((p) => ts.isJsxSpreadAttribute(p))) return false;
  return props.some(
    (p) =>
      ts.isJsxAttribute(p) &&
      p.name.getText() === "prefetch" &&
      p.initializer !== undefined &&
      ts.isJsxExpression(p.initializer) &&
      p.initializer.expression?.kind === ts.SyntaxKind.FalseKeyword,
  );
}

describe("next/link", () => {
  it("every <Link> passes prefetch={false}", () => {
    let seen = 0;
    const offenders = componentFiles().flatMap((file) => {
      const sf = sourceFile(file);
      const name = linkLocalName(sf);
      if (name === null) return [];
      return nodesOf(sf)
        .filter(
          (n): n is ts.JsxOpeningElement | ts.JsxSelfClosingElement =>
            (ts.isJsxOpeningElement(n) || ts.isJsxSelfClosingElement(n)) &&
            n.tagName.getText() === name,
        )
        .filter((n) => {
          seen += 1;
          return !hasPrefetchFalse(n.attributes);
        })
        .map(
          (n) => `${file}:${sf.getLineAndCharacterOfPosition(n.pos).line + 1}`,
        );
    });
    expect(offenders).toEqual([]);
    expect(seen).toBeGreaterThan(20);
  });
});

describe("component imports", () => {
  it("reach @/server only through a type-only import of the barrel", () => {
    const offenders = componentFiles().flatMap((file) =>
      moduleSpecifiers(sourceFile(file))
        .filter(({ spec }) => spec.startsWith("@/server"))
        .filter(
          ({ spec, node }) =>
            spec !== "@/server/pick-board" ||
            !ts.isImportDeclaration(node) ||
            node.importClause?.isTypeOnly !== true,
        )
        .map(({ spec }) => `${file}: ${spec}`),
    );
    expect(offenders).toEqual([]);
  });

  it("never import server-only modules or drivers", () => {
    const banned = /^(server-only|pg|drizzle-orm|next\/headers)(\/|$)/;
    const offenders = componentFiles().flatMap((file) =>
      moduleSpecifiers(sourceFile(file))
        .filter(({ spec }) => banned.test(spec))
        .map(({ spec }) => `${file}: ${spec}`),
    );
    expect(offenders).toEqual([]);
  });

  it("client components pull in only the small pure modules they need", () => {
    const clientFiles = componentFiles().filter((file) =>
      /^\s*(\/\/[^\n]*\n\s*)*["']use client["']/.test(read(file)),
    );
    expect(clientFiles.sort()).toEqual([
      `${COMPONENTS_DIR}/glossary.tsx`,
      `${COMPONENTS_DIR}/queyu-button.tsx`,
    ]);
    const allowed = new Set([
      "react",
      "@/core/pick-board/glossary",
      "@/core/pick-board/queyu",
    ]);
    for (const file of clientFiles)
      for (const { spec } of moduleSpecifiers(sourceFile(file)))
        expect(allowed.has(spec)).toBe(true);
  });
});

const COLOR_CLASS =
  /(?<![\w-])(?:text|bg|border|ring|fill|stroke|decoration|outline)-(helper|ink-1|ink-2|ink-dim|brand-hover|brand|on-brand|gold|line-strong|line|panel-hover|panel|raised|(?:warning|success|danger|info|violet)-(?:surface|ink|line))(?![\w-])/g;

/**
 * Colors the board must not borrow: Tailwind's default palette and the
 * workspace's own design tokens (they follow the workspace theme, not the
 * board's light / dark variables).
 */
const FOREIGN_COLOR_CLASS =
  /(?<![\w-])(?:text|bg|border|ring|fill|stroke|decoration|outline|divide|placeholder|from|via|to|accent|caret|shadow)-(?:(?:slate|gray|zinc|neutral|stone|red|orange|amber|yellow|lime|green|emerald|teal|cyan|sky|blue|indigo|violet|purple|fuchsia|pink|rose)-\d{2,3}|black|background|foreground|card|popover|primary|secondary|muted|accent|destructive|border|input|ring|sidebar)(?:-foreground)?(?:\/\d+)?(?![\w-])/g;

/** Tailwind's own theme variables that the components may read. */
const TAILWIND_VARIABLES = new Set(["font-mono"]);

describe("colors", () => {
  it("every pick-board color utility is mapped in the theme file", () => {
    const theme = read(THEME_CSS);
    const missing = componentFiles().flatMap((file) =>
      textsOf(sourceFile(file)).flatMap((text) =>
        [...text.matchAll(COLOR_CLASS)]
          .map((m) => m[1] ?? "")
          .filter((name) => !theme.includes(`--color-${name}: var(--${name});`))
          .map((name) => `${file}: ${name}`),
      ),
    );
    expect(missing).toEqual([]);
  });

  it("every var(--…) is a pick-board variable or a Tailwind theme variable", () => {
    const board = read(BOARD_CSS);
    const lightBlock = /\.pick-board \{([\s\S]*?)\n\}/.exec(board)?.[1] ?? "";
    let seen = 0;
    const missing = componentFiles().flatMap((file) =>
      textsOf(sourceFile(file)).flatMap((text) =>
        [...text.matchAll(/var\(--([\w-]+)/g)]
          .map((m) => m[1] ?? "")
          .filter((name) => {
            seen += 1;
            return (
              !TAILWIND_VARIABLES.has(name) &&
              !lightBlock.includes(`--${name}:`)
            );
          })
          .map((name) => `${file}: ${name}`),
      ),
    );
    expect(missing).toEqual([]);
    expect(seen).toBeGreaterThan(10);
  });

  it("no color utilities from outside the board: no Tailwind palette, no workspace tokens", () => {
    const offenders = componentFiles().flatMap((file) =>
      textsOf(sourceFile(file)).flatMap((text) =>
        [...text.matchAll(FOREIGN_COLOR_CLASS)].map((m) => `${file}: ${m[0]}`),
      ),
    );
    expect(offenders).toEqual([]);
  });

  it("no literal colors: no white utilities and no hex values", () => {
    const offenders = componentFiles().flatMap((file) =>
      textsOf(sourceFile(file))
        .filter(
          (text) =>
            /\b(?:bg|text|border)-white\b|\/white\b/.test(text) ||
            /#[0-9a-fA-F]{3,8}\b/.test(text),
        )
        .map((text) => `${file}: ${text.slice(0, 60)}`),
    );
    expect(offenders).toEqual([]);
  });
});

describe("links and time", () => {
  it("no relative /admin/pick link outside the rule rewriter", () => {
    const rewriter = `${CORE_DIR}/rules.ts`;
    const offenders = boardFiles().flatMap((file) =>
      textsOf(sourceFile(file))
        .filter((text) => /(?<!dramashortstv\.com)\/admin\/pick/.test(text))
        .filter((text) => !(file === rewriter && text === "/admin/pick?"))
        .map((text) => `${file}: ${text}`),
    );
    expect(offenders).toEqual([]);
  });

  it("no argument-less new Date(): the board's now is the version's as_of", () => {
    const offenders = boardFiles().flatMap((file) => {
      const sf = sourceFile(file);
      return nodesOf(sf)
        .filter(
          (n) =>
            ts.isNewExpression(n) &&
            ts.isIdentifier(n.expression) &&
            n.expression.text === "Date" &&
            (n.arguments?.length ?? 0) === 0,
        )
        .map(
          (n) => `${file}:${sf.getLineAndCharacterOfPosition(n.pos).line + 1}`,
        );
    });
    expect(offenders).toEqual([]);
  });
});

const PORTED: Readonly<Record<string, string>> = {
  "accounts-table.tsx": "accounts-table.tsx",
  "cells.tsx": "cells.tsx",
  "glossary.tsx": "glossary.tsx",
  "orders-table.tsx": "bill-table.tsx",
  "posted-record.tsx": "posted-record.tsx",
  "posted-table.tsx": "posted-table.tsx",
  "queyu-button.tsx": "queyu-button.tsx",
  "rank-filters.tsx": "rank-filters.tsx",
  "rank-table.tsx": "rank-table.tsx",
  "reelshort-cells.tsx": "reelshort-cells.tsx",
  "reelshort-detail.tsx": "reelshort-detail.tsx",
  "reelshort-spark.tsx": "reelshort-spark.tsx",
  "reelshort-table.tsx": "reelshort-table.tsx",
  "row-detail.tsx": "row-detail.tsx",
  "rows-table.tsx": "rows-table.tsx",
  "rules-tab.tsx": "rules-tab.tsx",
  "sources.tsx": "sources.tsx",
  "spark.tsx": "spark.tsx",
  "toolbar.tsx": "toolbar.tsx",
};

describe("the ported components", () => {
  it("all nineteen RealShort components are here, each naming its source", () => {
    const names = readdirSync(path.join(FRONTEND_ROOT, COMPONENTS_DIR)).filter(
      (name) => name.endsWith(".tsx"),
    );
    for (const [name, source] of Object.entries(PORTED)) {
      expect(names).toContain(name);
      expect(read(`${COMPONENTS_DIR}/${name}`)).toMatch(
        new RegExp(
          `^// PORTED_FROM: realshort@816ca2e src/components/admin/pick/${source.replace(".", "\\.")}\\b`,
        ),
      );
    }
  });
});
