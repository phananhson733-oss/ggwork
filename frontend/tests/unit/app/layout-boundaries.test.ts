import { readFileSync, readdirSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "@rstest/core";

const FRONTEND_ROOT = path.resolve(__dirname, "../../..");

function source(relativePath: string) {
  return readFileSync(path.join(FRONTEND_ROOT, relativePath), "utf8");
}

const PICK_DATA_LAYOUT = "src/app/workspace/pick-data/layout.tsx";
const PICK_BOARD_STYLES = "src/app/workspace/pick-data/pick-board.css";
const PICK_BOARD_THEME = "src/styles/pick-board-theme.css";
const GLOBAL_STYLES = "src/styles/globals.css";

const PICK_BOARD_COLORS = [
  "helper",
  "ink-1",
  "ink-2",
  "ink-dim",
  "brand",
  "brand-hover",
  "on-brand",
  "gold",
  "line",
  "line-strong",
  "panel",
  "panel-hover",
  "raised",
  "warning-surface",
  "warning-ink",
  "warning-line",
  "success-surface",
  "success-ink",
  "danger-surface",
  "danger-line",
  "info-surface",
  "info-ink",
  "violet-surface",
  "violet-ink",
] as const;

type PickBoardVariable =
  | (typeof PICK_BOARD_COLORS)[number]
  | "chart-series-revenue"
  | "chart-series-promoters";

// RealShort 816ca2e, src/app/globals.css:218-302 (:root.admin-light).
const LIGHT_PALETTE: Readonly<Record<PickBoardVariable, string>> = {
  helper: "#6f6579",
  "ink-1": "#1b1522",
  "ink-2": "#3e3546",
  "ink-dim": "#8e8397",
  brand: "#c8204f",
  "brand-hover": "#e0305f",
  "on-brand": "#ffffff",
  gold: "#8a5a00",
  line: "rgb(27 21 34 / 10%)",
  "line-strong": "rgb(27 21 34 / 16%)",
  panel: "#ffffff",
  "panel-hover": "#f8f6fa",
  raised: "#f3f0f6",
  "warning-surface": "#fbf0d9",
  "warning-ink": "#8a5a00",
  "warning-line": "rgb(138 90 0 / 30%)",
  "success-surface": "#e3f5e9",
  "success-ink": "#1f7a45",
  "danger-surface": "#fde7ec",
  "danger-line": "rgb(200 32 79 / 35%)",
  "info-surface": "#e3eefb",
  "info-ink": "#1d4f8f",
  "violet-surface": "#ece6fb",
  "violet-ink": "#5b3fd6",
  "chart-series-revenue": "#c8204f",
  "chart-series-promoters": "#1f7a45",
};

// RealShort 816ca2e, src/app/globals.css:114-217 (dark admin palette).
const REALSHORT_DARK_PALETTE: Readonly<Record<PickBoardVariable, string>> = {
  helper: "#a398ab",
  "ink-1": "#f0eaf2",
  "ink-2": "#d3cbd8",
  "ink-dim": "#6f6478",
  brand: "#ff3d71",
  "brand-hover": "#ff6188",
  "on-brand": "#0b0810",
  gold: "#ffc24b",
  line: "rgb(255 255 255 / 10%)",
  "line-strong": "rgb(255 255 255 / 15%)",
  panel: "#120e18",
  "panel-hover": "#16111e",
  raised: "#17121f",
  "warning-surface": "#3a2e16",
  "warning-ink": "#e8c07d",
  "warning-line": "rgb(232 192 125 / 30%)",
  "success-surface": "#16321f",
  "success-ink": "#7dd8a0",
  "danger-surface": "#3a1620",
  "danger-line": "rgb(255 61 113 / 40%)",
  "info-surface": "#0f2438",
  "info-ink": "#7fb8ff",
  "violet-surface": "#1e1638",
  "violet-ink": "#b9a6ff",
  "chart-series-revenue": "#3987e5",
  "chart-series-promoters": "#199e70",
};

// Documented departures: RealShort's dark panels sit on a #0b0810 page, so
// they follow the workbench's card and muted surfaces here, and ink-dim is one
// step lighter to keep its contrast on them.
const DARK_PALETTE: Readonly<Record<PickBoardVariable, string>> = {
  ...REALSHORT_DARK_PALETTE,
  panel: "var(--card)",
  "panel-hover": "var(--muted)",
  raised: "var(--muted)",
  "ink-dim": "#7a6f84",
};

const PICK_BOARD_VARIABLES = Object.keys(LIGHT_PALETTE);
const SCANNED_EXTENSIONS = new Set([".css", ".js", ".jsx", ".ts", ".tsx"]);

function listSourceFiles(directory = "src"): string[] {
  const absolute = path.join(FRONTEND_ROOT, directory);
  return readdirSync(absolute, { withFileTypes: true }).flatMap((entry) => {
    const relativePath = path.posix.join(directory, entry.name);
    if (entry.isDirectory()) return listSourceFiles(relativePath);
    return SCANNED_EXTENSIONS.has(path.extname(entry.name))
      ? [relativePath]
      : [];
  });
}

// Strings are matched first so a "/*" or "//" inside one is left alone.
const CSS_COMMENT =
  /("(?:[^"\\\n]|\\.)*"|'(?:[^'\\\n]|\\.)*')|\/\*[\s\S]*?\*\//g;
const SCRIPT_COMMENT =
  /("(?:[^"\\\n]|\\.)*"|'(?:[^'\\\n]|\\.)*'|`(?:[^`\\]|\\.)*`)|\/\*[\s\S]*?\*\/|\/\/[^\n]*/g;

function withoutComments(css: string) {
  return css.replace(CSS_COMMENT, (_match, text?: string) => text ?? "").trim();
}

function withoutScriptComments(script: string) {
  return script
    .replace(SCRIPT_COMMENT, (_match, text?: string) => text ?? "")
    .trim();
}

function importSpecifiers(file: string) {
  const text = source(file);
  const pattern = file.endsWith(".css")
    ? /@import\s+(?:url\(\s*)?["']([^"']+)["']/g
    : /(?:\bfrom|\bimport|\brequire)\s*\(?\s*["']([^"']+)["']/g;
  const code = file.endsWith(".css")
    ? withoutComments(text)
    : withoutScriptComments(text);
  return [...code.matchAll(pattern)].map((match) => match[1] ?? "");
}

function resolveSpecifier(file: string, specifier: string) {
  if (specifier.startsWith("@/")) return `src/${specifier.slice(2)}`;
  if (!specifier.startsWith(".")) return specifier;
  return path.posix.join(path.posix.dirname(file), specifier);
}

function importersOf(target: string) {
  return listSourceFiles().filter((file) =>
    importSpecifiers(file).some(
      (specifier) => resolveSpecifier(file, specifier) === target,
    ),
  );
}

function cssRules(css: string) {
  return [...withoutComments(css).matchAll(/([^{}]+)\{([^{}]*)\}/g)].map(
    (match) => ({
      selector: (match[1] ?? "").trim(),
      body: match[2] ?? "",
    }),
  );
}

function declarations(body: string) {
  const pairs = body
    .split(";")
    .map((declaration) => declaration.trim())
    .filter(Boolean)
    .map((declaration) => {
      const colon = declaration.indexOf(":");
      return [
        declaration.slice(0, colon).trim(),
        declaration.slice(colon + 1).trim(),
      ] as const;
    });
  return [...pairs].sort(([a], [b]) => a.localeCompare(b));
}

function expectedDeclarations(palette: Readonly<Record<string, string>>) {
  return declarations(
    Object.entries(palette)
      .map(([name, value]) => `--${name}: ${value};`)
      .join(""),
  );
}

function ruleDeclarations(css: string, selector: string) {
  const rule = cssRules(css).find((entry) => entry.selector === selector);
  return declarations(rule?.body ?? "");
}

describe("layout performance boundaries", () => {
  it("keeps request locale and rich-content styles out of the root layout", () => {
    const rootLayout = source("src/app/layout.tsx");

    expect(rootLayout).not.toContain("detectLocaleServer");
    expect(rootLayout).not.toContain("I18nProvider");
    expect(rootLayout).not.toContain("katex/dist/katex.min.css");
    expect(rootLayout).not.toContain("streamdown/styles.css");
    expect(rootLayout).toContain("DEFAULT_LOCALE");
  });

  it("assigns rich-content styles to routes that render them", () => {
    expect(source("src/app/workspace/layout.tsx")).toContain(
      'import "streamdown/styles.css"',
    );
    expect(source("src/app/[lang]/docs/layout.tsx")).toContain(
      'import "katex/dist/katex.min.css"',
    );
    expect(source("src/app/blog/layout.tsx")).toContain(
      'import "katex/dist/katex.min.css"',
    );
    const artifactViewerLayout = source("src/app/artifacts/view/layout.tsx");
    expect(artifactViewerLayout).toContain('import "streamdown/styles.css"');
    expect(artifactViewerLayout).toContain('import "katex/dist/katex.min.css"');
  });

  it("passes only serializable locale state through server layouts", () => {
    for (const layout of [
      source("src/app/(auth)/layout.tsx"),
      source("src/app/workspace/layout.tsx"),
      source("src/app/artifacts/view/layout.tsx"),
    ]) {
      expect(layout).toContain("detectLocaleServer");
      expect(layout).not.toContain("initialTranslations");
      expect(layout).not.toContain("getI18n");
    }
  });
});

describe("pick-board palette boundaries", () => {
  it("loads the palette values only through the pick-data layout", () => {
    expect(importersOf(PICK_BOARD_STYLES)).toEqual([PICK_DATA_LAYOUT]);
    for (const layout of [
      "src/app/layout.tsx",
      "src/app/workspace/layout.tsx",
    ]) {
      expect(source(layout)).not.toContain("pick-board");
    }
  });

  it("keeps the pick-data layout a style import that renders children as-is", () => {
    expect(importSpecifiers(PICK_DATA_LAYOUT)).toEqual(["./pick-board.css"]);
    expect(withoutScriptComments(source(PICK_DATA_LAYOUT))).toMatch(
      /\)\s*\{\s*return children;\s*\}$/,
    );
  });

  it("maps the color utilities globally without defining their values", () => {
    expect(importSpecifiers(GLOBAL_STYLES)).toEqual([
      "tailwindcss",
      "tw-animate-css",
      "./pick-board-theme.css",
    ]);
    expect(importersOf(PICK_BOARD_THEME)).toEqual([GLOBAL_STYLES]);

    const theme = source(PICK_BOARD_THEME);
    expect(cssRules(theme).map((rule) => rule.selector)).toEqual([
      "@theme inline",
    ]);
    expect(withoutComments(theme)).toMatch(/^@theme inline \{[^{}]*\}$/);
    expect(ruleDeclarations(theme, "@theme inline")).toEqual(
      declarations(
        PICK_BOARD_COLORS.map(
          (name) => `--color-${name}: var(--${name});`,
        ).join(""),
      ),
    );
  });

  it("defines the raw variables only on .pick-board", () => {
    const routeStyles = source(PICK_BOARD_STYLES);
    expect(withoutComments(routeStyles)).not.toMatch(/@/);
    expect(cssRules(routeStyles).map((rule) => rule.selector)).toEqual([
      ".pick-board",
      ".dark .pick-board",
    ]);

    const rawDefinition = new RegExp(
      `--(?:${PICK_BOARD_VARIABLES.join("|")})\\s*:`,
    );
    for (const file of listSourceFiles()) {
      if (file === PICK_BOARD_STYLES || !file.endsWith(".css")) continue;
      expect({ file, defines: rawDefinition.test(source(file)) }).toEqual({
        file,
        defines: false,
      });
    }
  });

  it("takes RealShort's values, with the documented dark-surface changes", () => {
    const routeStyles = source(PICK_BOARD_STYLES);
    expect(ruleDeclarations(routeStyles, ".pick-board")).toEqual(
      expectedDeclarations(LIGHT_PALETTE),
    );
    expect(ruleDeclarations(routeStyles, ".dark .pick-board")).toEqual(
      expectedDeclarations(DARK_PALETTE),
    );
  });
});
