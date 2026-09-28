import { existsSync, readFileSync, readdirSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "@rstest/core";

const FRONTEND_ROOT = path.resolve(__dirname, "../../..");

function source(relativePath: string) {
  return readFileSync(path.join(FRONTEND_ROOT, relativePath), "utf8");
}

const ROOT_LAYOUT = "src/app/layout.tsx";
const GLOBAL_STYLES = "src/styles/globals.css";
const THEME_STYLES = "src/styles/ggwork-theme.css";

// Removed when the palette went global; nothing may bring them back.
const RETIRED_PALETTE_FILES = [
  "src/app/workspace/pick-data/layout.tsx",
  "src/app/workspace/pick-data/pick-board.css",
  "src/styles/pick-board-theme.css",
] as const;

// Claude Design "GGWork 设计系统规范化", tokens/ggwork-theme.css, direction 1c.
const DESIGN_LIGHT: Readonly<Record<string, string>> = {
  bg: "#f7f9fb",
  surface: "#ffffff",
  raised: "#f1f4f7",
  hover: "#f4f7f9",
  "sidebar-bg": "#f2f5f7",
  line: "#e3e8ee",
  "line-strong": "#cdd5de",
  "ink-1": "#0e1621",
  "ink-2": "#36414d",
  helper: "#5a6675",
  "ink-dim": "#8995a3",
  brand: "#0e1621",
  "brand-hover": "#26313d",
  "on-brand": "#ffffff",
  link: "#0b7468",
  "brand-soft": "#e3f3f0",
  "brand-ink": "#0a5f55",
  "brand-line": "#bfe2db",
  cta: "linear-gradient(90deg, #3ddc97, #4fc3f7)",
  "cta-ink": "#06231a",
  "success-surface": "#e3f4ea",
  "success-ink": "#1f7a45",
  "warning-surface": "#fbf0d9",
  "warning-ink": "#8a5a00",
  "warning-line": "rgb(138 90 0 / 30%)",
  "danger-surface": "#fdecea",
  "danger-ink": "#b42318",
  "danger-line": "rgb(180 35 24 / 30%)",
  "info-surface": "#e4eff8",
  "info-ink": "#1d5c8f",
};

// The design's .dark block; --cta and --cta-ink carry over from :root.
const DESIGN_DARK: Readonly<Record<string, string>> = {
  bg: "#0c1115",
  surface: "#11181d",
  raised: "#172027",
  hover: "#151d23",
  "sidebar-bg": "#0f151a",
  line: "#222c34",
  "line-strong": "#2f3b45",
  "ink-1": "#e7edf1",
  "ink-2": "#c2ccd4",
  helper: "#93a0ab",
  "ink-dim": "#6b7884",
  brand: "#e7edf1",
  "brand-hover": "#ffffff",
  "on-brand": "#0c1115",
  link: "#4fd8bd",
  "brand-soft": "#10272a",
  "brand-ink": "#7fe6d1",
  "brand-line": "#1d4540",
  "success-surface": "#14301f",
  "success-ink": "#7dd8a0",
  "warning-surface": "#362b15",
  "warning-ink": "#e8c07d",
  "warning-line": "rgb(232 192 125 / 30%)",
  "danger-surface": "#3a1818",
  "danger-ink": "#ff8a80",
  "danger-line": "rgb(255 138 128 / 35%)",
  "info-surface": "#10233a",
  "info-ink": "#8cc3f5",
};

// shadcn and former pick-board names resolve to the raw tokens on :root only,
// so .dark and data-brand switch them without redefining the aliases.
const DESIGN_ALIASES: Readonly<Record<string, string>> = {
  background: "var(--bg)",
  foreground: "var(--ink-1)",
  card: "var(--surface)",
  "card-foreground": "var(--ink-1)",
  popover: "var(--surface)",
  "popover-foreground": "var(--ink-1)",
  primary: "var(--brand)",
  "primary-foreground": "var(--on-brand)",
  secondary: "var(--raised)",
  "secondary-foreground": "var(--ink-1)",
  muted: "var(--raised)",
  "muted-foreground": "var(--helper)",
  accent: "var(--hover)",
  "accent-foreground": "var(--ink-1)",
  destructive: "var(--danger-ink)",
  border: "var(--line)",
  input: "var(--line-strong)",
  ring: "var(--link)",
  sidebar: "var(--sidebar-bg)",
  "sidebar-foreground": "var(--ink-2)",
  "sidebar-primary": "var(--brand)",
  "sidebar-primary-foreground": "var(--on-brand)",
  "sidebar-accent": "var(--brand-soft)",
  "sidebar-accent-foreground": "var(--brand-ink)",
  "sidebar-border": "var(--line)",
  "sidebar-ring": "var(--link)",
  panel: "var(--surface)",
  "panel-hover": "var(--hover)",
  gold: "var(--warning-ink)",
  "violet-surface": "var(--info-surface)",
  "violet-ink": "var(--info-ink)",
};

// The 1a/1b alternates, switched with <html data-brand="evergreen|tide">.
const DESIGN_ALTERNATES: Readonly<Record<string, Record<string, string>>> = {
  '[data-brand="tide"]': {
    brand: "#0a659a",
    "brand-hover": "#08547f",
    "on-brand": "#ffffff",
    link: "#0a659a",
    "brand-soft": "#e3eff7",
    "brand-ink": "#084c73",
    "brand-line": "#bcd7ea",
    cta: "linear-gradient(#0a659a, #0a659a)",
    "cta-ink": "#ffffff",
  },
  '.dark[data-brand="tide"], [data-brand="tide"] .dark': {
    brand: "#56b4e6",
    "brand-hover": "#79c5ee",
    "on-brand": "#04121b",
    link: "#79c5ee",
    "brand-soft": "#0f2230",
    "brand-ink": "#9dd3f1",
    "brand-line": "#1c3a50",
    cta: "linear-gradient(#56b4e6, #56b4e6)",
    "cta-ink": "#04121b",
  },
  '[data-brand="evergreen"]': {
    brand: "#0e7a55",
    "brand-hover": "#0b6446",
    "on-brand": "#ffffff",
    link: "#0e7a55",
    "brand-soft": "#e4f3ec",
    "brand-ink": "#0b5e42",
    "brand-line": "#bfe3d2",
    cta: "linear-gradient(#0e7a55, #0e7a55)",
    "cta-ink": "#ffffff",
  },
  '.dark[data-brand="evergreen"], [data-brand="evergreen"] .dark': {
    brand: "#3ccb8e",
    "brand-hover": "#5cd9a2",
    "on-brand": "#04140c",
    link: "#5cd9a2",
    "brand-soft": "#12291f",
    "brand-ink": "#7ee3b5",
    "brand-line": "#1e4534",
    cta: "linear-gradient(#3ccb8e, #3ccb8e)",
    "cta-ink": "#04140c",
  },
};

// Utilities the pick board used before the palette went global, plus the
// ones the design adds (links, selection, surfaces, danger text).
const MAPPED_COLORS = [
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
  "link",
  "brand-soft",
  "brand-ink",
  "brand-line",
  "surface",
  "hover",
  "danger-ink",
] as const;

const THEME_VARIABLES = [
  "radius",
  ...Object.keys(DESIGN_LIGHT),
  ...Object.keys(DESIGN_ALIASES),
  "chart-series-revenue",
  "chart-series-promoters",
];

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
      selector: (match[1] ?? "").trim().replace(/\s+/g, " "),
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
        declaration
          .slice(colon + 1)
          .trim()
          .replace(/\s+/g, " "),
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

/** Every declaration of the rules with this selector, by property name. */
function declarationMap(css: string, selector: string) {
  return Object.fromEntries(
    cssRules(css)
      .filter((rule) => rule.selector === selector)
      .flatMap((rule) => declarations(rule.body)),
  ) as Record<string, string>;
}

/** The value each of `names` has in `map`, lower-cased for hex colors. */
function pick(map: Record<string, string>, names: readonly string[]) {
  return Object.fromEntries(
    names.map((name) => [name, map[`--${name}`]?.toLowerCase()]),
  );
}

function themeMapping() {
  return {
    ...declarationMap(source(GLOBAL_STYLES), "@theme inline"),
    ...declarationMap(source(THEME_STYLES), "@theme inline"),
  };
}

interface FontLoader {
  readonly src: string;
  readonly variable: string;
}

/** `src` and `variable` of each `const x = localFont({...})`, by const name. */
function fontLoaders(layout: string): Record<string, FontLoader> {
  return Object.fromEntries(
    [
      ...withoutScriptComments(layout).matchAll(
        /\bconst\s+(\w+)\s*=\s*localFont\(\{([^{}]*)\}\)/g,
      ),
    ].map((match) => {
      const body = match[2] ?? "";
      return [
        match[1] ?? "",
        {
          src: /\bsrc:\s*"([^"]+)"/.exec(body)?.[1] ?? "",
          variable: /\bvariable:\s*"(--[\w-]+)"/.exec(body)?.[1] ?? "",
        },
      ];
    }),
  );
}

function referencedVariables(value = "") {
  return [...value.matchAll(/var\((--[\w-]+)/g)].map((match) => match[1]);
}

describe("layout performance boundaries", () => {
  it("keeps request locale and rich-content styles out of the root layout", () => {
    const rootLayout = source(ROOT_LAYOUT);

    expect(rootLayout).not.toContain("detectLocaleServer");
    expect(rootLayout).not.toContain("I18nProvider");
    expect(rootLayout).not.toContain("katex/dist/katex.min.css");
    expect(rootLayout).not.toContain("streamdown/styles.css");
    expect(rootLayout).toContain("DEFAULT_LOCALE");
    expect(importSpecifiers(ROOT_LAYOUT)).toContain("@/core/i18n/locale");
    expect(
      importSpecifiers(ROOT_LAYOUT).filter((specifier) =>
        specifier.endsWith(".css"),
      ),
    ).toEqual(["@/styles/globals.css"]);
  });

  it("loads the brand fonts and name only in the root layout", () => {
    expect(importSpecifiers(ROOT_LAYOUT)).toEqual(
      expect.arrayContaining(["next/font/local", "@/core/brand"]),
    );
    // next/font/google downloads at build time, which fails behind the
    // mirrors and air-gapped hosts the Docker build supports.
    expect(importersOf("next/font/google")).toEqual([]);
    // next/font has no runtime module, so anything else importing it would
    // also break under rstest; one call site also keeps one font set.
    expect(importersOf("next/font/local")).toEqual([ROOT_LAYOUT]);

    const loaders = fontLoaders(source(ROOT_LAYOUT));
    expect(Object.keys(loaders).sort()).toEqual(["dmSans", "jetBrainsMono"]);
    for (const { src } of Object.values(loaders)) {
      expect(src).toMatch(/^\.\/fonts\/[\w-]+\.woff2$/);
      expect(
        existsSync(path.join(FRONTEND_ROOT, "src/app", src)),
        `${src} is committed`,
      ).toBe(true);
    }
    const mapping = themeMapping();
    expect(referencedVariables(mapping["--font-sans"])).toEqual([
      loaders.dmSans?.variable,
    ]);
    // The design's CJK face comes first among the installed Chinese fonts.
    expect(mapping["--font-sans"]).toMatch(
      /^var\(--font-dm-sans[^)]*\),\s*"Noto Sans SC",/,
    );
    expect(referencedVariables(mapping["--font-mono"])).toEqual([
      loaders.jetBrainsMono?.variable,
    ]);
  });

  it("matches the browser theme color to the page background", () => {
    const themeColors = Object.fromEntries(
      [
        ...withoutScriptComments(source(ROOT_LAYOUT)).matchAll(
          /media:\s*"\(prefers-color-scheme:\s*(light|dark)\)",\s*color:\s*"([^"]+)"/g,
        ),
      ].map((match) => [match[1] ?? "", (match[2] ?? "").toLowerCase()]),
    );
    const theme = source(THEME_STYLES);
    expect(themeColors).toEqual({
      light: pick(declarationMap(theme, ":root"), ["bg"]).bg,
      dark: pick(declarationMap(theme, ".dark"), ["bg"]).bg,
    });
  });

  it("assigns rich-content styles to routes that render them", () => {
    const workspaceLayout = source("src/app/workspace/layout.tsx");
    expect(workspaceLayout).toContain('import "streamdown/styles.css"');
    expect(workspaceLayout).toContain('import "katex/dist/katex.min.css"');
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

describe("GGWork theme boundaries", () => {
  it("loads the theme once, through the global stylesheet", () => {
    expect(importSpecifiers(GLOBAL_STYLES)).toEqual([
      "tailwindcss",
      "tw-animate-css",
      "./ggwork-theme.css",
    ]);
    expect(importersOf(THEME_STYLES)).toEqual([GLOBAL_STYLES]);
    expect(importersOf(GLOBAL_STYLES)).toEqual([ROOT_LAYOUT]);
  });

  it("no longer ships the route-scoped pick-board palette", () => {
    const leftovers = RETIRED_PALETTE_FILES.filter((file) =>
      existsSync(path.join(FRONTEND_ROOT, file)),
    );
    expect(leftovers).toEqual([]);
  });

  it("takes the design's light and dark values", () => {
    const theme = source(THEME_STYLES);
    const light = declarationMap(theme, ":root");
    const dark = declarationMap(theme, ".dark");

    expect(pick(light, Object.keys(DESIGN_LIGHT))).toEqual(DESIGN_LIGHT);
    expect(pick(dark, Object.keys(DESIGN_DARK))).toEqual(DESIGN_DARK);
    expect(light["--radius"]).toBe("0.5rem");
  });

  it("points the shadcn and former pick-board names at the tokens on :root", () => {
    const theme = source(THEME_STYLES);
    const aliases = Object.keys(DESIGN_ALIASES);

    expect(pick(declarationMap(theme, ":root"), aliases)).toEqual(
      DESIGN_ALIASES,
    );
    // Dark mode swaps raw tokens only; aliases defined there would pin a
    // component to one mode.
    expect(
      aliases.filter((name) => `--${name}` in declarationMap(theme, ".dark")),
    ).toEqual([]);
  });

  it("keeps the 1a and 1b alternates behind data-brand", () => {
    const theme = source(THEME_STYLES);
    for (const [selector, values] of Object.entries(DESIGN_ALTERNATES)) {
      expect({
        selector,
        declarations: ruleDeclarations(theme, selector),
      }).toEqual({ selector, declarations: expectedDeclarations(values) });
    }
  });

  it("maps the former pick-board utilities and the design's new ones globally", () => {
    const mapping = themeMapping();
    const expected = Object.fromEntries(
      MAPPED_COLORS.map((name) => [`--color-${name}`, `var(--${name})`]),
    );

    expect(
      Object.fromEntries(
        Object.keys(expected).map((name) => [name, mapping[name]]),
      ),
    ).toEqual(expected);
    // --cta is a gradient in 1c and a flat color in 1a/1b: bg-cta, not a color.
    expect(mapping["--background-image-cta"]).toBe("var(--cta)");
    expect(mapping["--color-cta"]).toBeUndefined();
  });

  it("maps every color utility to a variable the theme defines on :root", () => {
    const defined = new Set(
      Object.keys(declarationMap(source(THEME_STYLES), ":root")),
    );
    const undefinedTargets = Object.entries(themeMapping())
      .filter(([name]) => name.startsWith("--color-"))
      .flatMap(([name, value]) =>
        referencedVariables(value)
          .filter((variable) => !defined.has(variable ?? ""))
          .map((variable) => `${name} -> ${variable}`),
      );
    expect(undefinedTargets).toEqual([]);
  });

  it("defines the palette only in the theme file, never on .pick-board", () => {
    const rawDefinition = new RegExp(
      `(?<![\\w-])--(?:${THEME_VARIABLES.join("|")})\\s*:`,
    );
    const stylesheets = listSourceFiles().filter((file) =>
      file.endsWith(".css"),
    );

    expect(
      stylesheets.filter(
        (file) =>
          file !== THEME_STYLES &&
          rawDefinition.test(withoutComments(source(file))),
      ),
    ).toEqual([]);
    expect(
      stylesheets.flatMap((file) =>
        cssRules(source(file))
          .filter(
            (rule) =>
              /\.pick-board\b/.test(rule.selector) &&
              declarations(rule.body).some(([name]) => name.startsWith("--")),
          )
          .map((rule) => `${file}: ${rule.selector}`),
      ),
    ).toEqual([]);
  });
});
