import { readFileSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "@rstest/core";

const FRONTEND_ROOT = path.resolve(__dirname, "../../..");

function source(relativePath: string) {
  return readFileSync(path.join(FRONTEND_ROOT, relativePath), "utf8");
}

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

const PICK_BOARD_VARIABLES = [
  ...PICK_BOARD_COLORS,
  "chart-series-revenue",
  "chart-series-promoters",
] as const;

function withoutComments(css: string) {
  return css.replace(/\/\*[\s\S]*?\*\//g, "").trim();
}

function ruleBody(css: string, selector: string) {
  const start = `\n${css}`.indexOf(`\n${selector} {`);
  if (start < 0) return "";
  const open = css.indexOf("{", start);
  return css.slice(open + 1, css.indexOf("}", open));
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

  it("keeps pick-board color values on the pick-data route", () => {
    const pickLayout = source("src/app/workspace/pick-data/layout.tsx");
    expect(pickLayout).toContain('import "./pick-board.css"');
    expect(pickLayout).not.toContain("@/core/auth");
    for (const layout of [
      "src/app/layout.tsx",
      "src/app/workspace/layout.tsx",
    ]) {
      expect(source(layout)).not.toContain("pick-board");
    }

    const globals = source("src/styles/globals.css");
    const theme = source("src/styles/pick-board-theme.css");
    const routeStyles = source("src/app/workspace/pick-data/pick-board.css");
    expect(globals).toContain('@import "./pick-board-theme.css";');
    expect(withoutComments(theme)).toMatch(/^@theme inline \{[^{}]*\}$/);
    expect(theme.match(/--color-/g)).toHaveLength(PICK_BOARD_COLORS.length);
    for (const name of PICK_BOARD_COLORS) {
      expect(theme).toContain(`--color-${name}: var(--${name});`);
    }

    expect(routeStyles).not.toMatch(/:root|@theme|@import/);
    const light = ruleBody(routeStyles, ".pick-board");
    const dark = ruleBody(routeStyles, ".dark .pick-board");
    for (const name of PICK_BOARD_VARIABLES) {
      expect(light).toContain(`--${name}:`);
      expect(dark).toContain(`--${name}:`);
      expect(globals).not.toContain(`--${name}:`);
      expect(theme).not.toContain(`--${name}:`);
    }
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
