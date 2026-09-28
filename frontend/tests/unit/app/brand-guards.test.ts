/**
 * Brand guards for the GGWork theme (Claude Design, direction 1c): DeerFlow's
 * decorative effects, serif wordmark, deer mask, beige palette and product
 * name must not come back through a copy-paste or a registry add.
 *
 * Internal identifiers stay as they are and are never matched here:
 * deerflow.* storage keys, deerflow_* thread metadata, DEER_FLOW_* env vars,
 * X-DeerFlow-* headers and the `deerflow` package name.
 *
 * Scripts are read on the TypeScript syntax tree, so comments never count;
 * stylesheets are read with their comments blanked out.
 */
import { readFileSync, readdirSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "@rstest/core";
import ts from "typescript";

const FRONTEND_ROOT = path.resolve(__dirname, "../../..");

// Mock API payloads and the static demo fixture are data, not UI. Public demo
// data (public/demo, plugin icons) is outside src.
const EXCLUDED = [/^src\/app\/mock\//, /^src\/core\/threads\/static-demo\.ts$/];

const SCRIPT_KINDS: Readonly<Record<string, ts.ScriptKind>> = {
  ".ts": ts.ScriptKind.TS,
  ".tsx": ts.ScriptKind.TSX,
  ".mts": ts.ScriptKind.TS,
  ".cts": ts.ScriptKind.TS,
  ".js": ts.ScriptKind.JS,
  ".jsx": ts.ScriptKind.JSX,
  ".mjs": ts.ScriptKind.JS,
  ".cjs": ts.ScriptKind.JS,
};
const SCANNED_EXTENSIONS = new Set([
  ...Object.keys(SCRIPT_KINDS),
  ".css",
  ".json",
]);

const LOCALE_FILES = [
  "src/core/i18n/locales/en-US.ts",
  "src/core/i18n/locales/zh-CN.ts",
] as const;
const GLOBAL_STYLES = [
  "src/styles/globals.css",
  "src/styles/ggwork-theme.css",
] as const;

interface Guard {
  readonly name: string;
  readonly pattern: RegExp;
}

const DEER_EMOJI: Guard = { name: "deer emoji", pattern: /\u{1F98C}/u };

// Deleted with the rebrand: gradient text, glow blur, aurora headline, shine
// border, confetti, the deer-shaped flickering-grid mask and the serif
// DeerFlow wordmark.
const RETIRED_LOOK: readonly Guard[] = [
  { name: "golden-text", pattern: /golden-text/ },
  { name: "ambilight", pattern: /ambilight/ },
  { name: "aurora text", pattern: /AuroraText|aurora-text/ },
  { name: "shine border", pattern: /ShineBorder|shine-border/ },
  {
    name: "confetti",
    pattern: /ConfettiButton|confetti-button|canvas-confetti/,
  },
  { name: "flickering grid", pattern: /FlickeringGrid|flickering-grid/ },
  { name: "deer.svg", pattern: /deer\.svg/ },
  { name: "font-serif", pattern: /\bfont-serif\b/ },
  DEER_EMOJI,
];

const OLD_PRODUCT_NAME: readonly Guard[] = [
  { name: "DeerFlow", pattern: /DeerFlow/ },
  DEER_EMOJI,
];

// Every script under src must say GGWork: the DeerFlow landing page, docs,
// blog and showcase were removed, so no public page keeps upstream copy.
// The About page credits the upstream project (pinned by about-content.test);
// the summarization middleware name is a backend graph node, not copy.
const APP_COPY_ALLOWED = [
  "src/components/workspace/settings/about-content.ts",
] as const;
const APP_PRODUCT_NAME: readonly Guard[] = [
  {
    name: "DeerFlow",
    pattern: /DeerFlow(?!SummarizationMiddleware)/,
  },
  DEER_EMOJI,
];

// DeerFlow's beige light palette (hue 87.47), its warm dark greys (hue
// 106.64) and the thin dark-mode text it set on .dark.
const RETIRED_STYLES: readonly Guard[] = [
  {
    name: "beige oklch (hue 87.47)",
    pattern: /oklch\(\s*[\d.]+%?\s+[\d.]+\s+87\.47\b[^)]*\)/,
  },
  {
    name: "warm dark oklch (hue 106.64)",
    pattern: /oklch\(\s*[\d.]+%?\s+[\d.]+\s+106\.64\b[^)]*\)/,
  },
  { name: "font-weight: 300", pattern: /font-weight\s*:\s*300\b/ },
];

function source(relativePath: string) {
  return readFileSync(path.join(FRONTEND_ROOT, relativePath), "utf8");
}

function listSourceFiles(directory = "src"): string[] {
  const absolute = path.join(FRONTEND_ROOT, directory);
  return readdirSync(absolute, { withFileTypes: true }).flatMap((entry) => {
    const relativePath = path.posix.join(directory, entry.name);
    if (EXCLUDED.some((pattern) => pattern.test(relativePath))) return [];
    if (entry.isDirectory()) return listSourceFiles(relativePath);
    return SCANNED_EXTENSIONS.has(path.extname(entry.name))
      ? [relativePath]
      : [];
  });
}

interface Piece {
  /** 1-based line where the piece starts. */
  readonly line: number;
  readonly text: string;
  /**
   * Whether line breaks in `text` are the file's own (whole stylesheets and
   * JSON). A string literal's "\n" escapes are not, so hits in it report the
   * literal's first line.
   */
  readonly sourceLines: boolean;
}

function parse(file: string, text: string) {
  return ts.createSourceFile(
    file,
    text,
    ts.ScriptTarget.Latest,
    true,
    SCRIPT_KINDS[path.extname(file)] ?? ts.ScriptKind.TS,
  );
}

function startLine(file: ts.SourceFile, node: ts.Node) {
  return file.getLineAndCharacterOfPosition(node.getStart(file)).line + 1;
}

/** String, template and JSX text plus identifiers: what the code says. */
function codeText(node: ts.Node): string | null {
  if (
    ts.isStringLiteral(node) ||
    ts.isNoSubstitutionTemplateLiteral(node) ||
    ts.isTemplateHead(node) ||
    ts.isTemplateMiddle(node) ||
    ts.isTemplateTail(node) ||
    ts.isJsxText(node) ||
    ts.isRegularExpressionLiteral(node) ||
    ts.isIdentifier(node) ||
    ts.isPrivateIdentifier(node)
  ) {
    return node.text;
  }
  return null;
}

function scriptPieces(
  file: string,
  text: string,
  select: (node: ts.Node) => string | null,
): Piece[] {
  const tree = parse(file, text);
  const pieces: Piece[] = [];
  const visit = (node: ts.Node) => {
    const piece = select(node);
    if (piece !== null) {
      pieces.push({
        line: startLine(tree, node),
        text: piece,
        sourceLines: false,
      });
    }
    ts.forEachChild(node, visit);
  };
  visit(tree);
  return pieces;
}

// Strings are matched first so a "/*" inside one is left alone. Comments
// keep their line breaks so reported line numbers stay right.
const CSS_COMMENT =
  /("(?:[^"\\\n]|\\.)*"|'(?:[^'\\\n]|\\.)*')|\/\*[\s\S]*?\*\//g;

function withoutCssComments(css: string) {
  return css.replace(
    CSS_COMMENT,
    (match, text?: string) => text ?? match.replace(/[^\n]/g, ""),
  );
}

function filePieces(file: string, text: string): Piece[] {
  const extension = path.extname(file);
  if (extension in SCRIPT_KINDS) return scriptPieces(file, text, codeText);
  const whole = extension === ".css" ? withoutCssComments(text) : text;
  return [{ line: 1, text: whole, sourceLines: true }];
}

function excerpt(text: string, index: number) {
  const start = Math.max(0, index - 24);
  const end = Math.min(text.length, index + 40);
  const body = text.slice(start, end).replace(/\s+/g, " ");
  return `${start > 0 ? "…" : ""}${body}${end < text.length ? "…" : ""}`;
}

/** `path:line name` for every guard match, optionally with the text around it. */
function hits(
  file: string,
  pieces: readonly Piece[],
  guards: readonly Guard[],
  withExcerpt = false,
): string[] {
  const found = pieces.flatMap(({ line, text, sourceLines }) =>
    guards.flatMap(({ name, pattern }) =>
      [...text.matchAll(new RegExp(pattern.source, `g${pattern.flags}`))].map(
        (match) => {
          const index = match.index ?? 0;
          const breaks = sourceLines
            ? (text.slice(0, index).match(/\n/g)?.length ?? 0)
            : 0;
          const detail = withExcerpt
            ? `: ${JSON.stringify(excerpt(text, index))}`
            : "";
          return {
            line: line + breaks,
            hit: `${file}:${line + breaks} ${name}${detail}`,
          };
        },
      ),
    ),
  );
  const ordered = [...found].sort((a, b) => a.line - b.line);
  return [...new Set(ordered.map(({ hit }) => hit))];
}

function retiredLookHits(file: string, text: string) {
  // Most files mention none of these; skip parsing them.
  if (!RETIRED_LOOK.some(({ pattern }) => pattern.test(text))) return [];
  return hits(file, filePieces(file, text), RETIRED_LOOK);
}

/**
 * Literal string values and JSX text; property names, module paths and types
 * are keys.
 */
function copyValue(node: ts.Node): string | null {
  if (ts.isJsxText(node)) return node.text;
  const isValue =
    ts.isStringLiteral(node) ||
    ts.isNoSubstitutionTemplateLiteral(node) ||
    ts.isTemplateHead(node) ||
    ts.isTemplateMiddle(node) ||
    ts.isTemplateTail(node);
  if (!isValue) return null;
  const parent = node.parent;
  const isKey =
    ((ts.isPropertyAssignment(parent) ||
      ts.isPropertySignature(parent) ||
      ts.isMethodDeclaration(parent)) &&
      parent.name === node) ||
    ts.isComputedPropertyName(parent) ||
    (ts.isElementAccessExpression(parent) &&
      parent.argumentExpression === node) ||
    ts.isImportDeclaration(parent) ||
    ts.isExportDeclaration(parent) ||
    ts.isExternalModuleReference(parent) ||
    ts.isLiteralTypeNode(parent);
  return isKey ? null : node.text;
}

function oldNameHits(
  file: string,
  text: string,
  guards: readonly Guard[] = OLD_PRODUCT_NAME,
) {
  return hits(file, scriptPieces(file, text, copyValue), guards, true);
}

function isScript(file: string) {
  return path.extname(file) in SCRIPT_KINDS;
}

describe("GGWork brand guards", () => {
  it("keeps DeerFlow's effects, serif wordmark, deer mask and emoji out of src", () => {
    const offenders = listSourceFiles().flatMap((file) =>
      retiredLookHits(file, source(file)),
    );
    expect(offenders).toEqual([]);
  });

  it("keeps the DeerFlow name and the deer emoji out of en-US and zh-CN copy", () => {
    const offenders = LOCALE_FILES.flatMap((file) =>
      oldNameHits(file, source(file)),
    );
    expect(offenders).toEqual([]);
  });

  it("keeps the DeerFlow name out of app copy", () => {
    const offenders = listSourceFiles()
      .filter(
        (file) =>
          isScript(file) &&
          !(APP_COPY_ALLOWED as readonly string[]).includes(file),
      )
      .flatMap((file) => oldNameHits(file, source(file), APP_PRODUCT_NAME));
    expect(offenders).toEqual([]);
  });

  it("keeps the beige palette and thin dark text out of the global styles", () => {
    const offenders = GLOBAL_STYLES.flatMap((file) =>
      hits(file, filePieces(file, source(file)), RETIRED_STYLES),
    );
    expect(offenders).toEqual([]);
  });

  it("matches what the code says, not comments or internal identifiers", () => {
    const script = [
      "// The ultra mode used golden-text and an AuroraText headline.",
      'localStorage.getItem("deerflow.sidebar-collapsed");',
      "const archived = metadata.deerflow_archived === true;",
      'headers.set("X-DeerFlow-Thread", id);',
      "const origins = process.env.DEER_FLOW_DEV_ALLOWED_ORIGINS;",
      'const title = <p className="font-serif">{name}</p>;',
    ].join("\n");
    expect(retiredLookHits("src/example.tsx", script)).toEqual([
      "src/example.tsx:6 font-serif",
    ]);

    const locale = [
      "export const copy = {",
      '  "DeerFlow": "GGWork",',
      '  storageKey: "deerflow.locale",',
      "  retry: (n: number) => `Attempt ${n}; DeerFlow retries.`,",
      "};",
    ].join("\n");
    expect(oldNameHits("src/core/i18n/locales/en-US.ts", locale)).toEqual([
      'src/core/i18n/locales/en-US.ts:4 DeerFlow: "; DeerFlow retries."',
    ]);

    const app = [
      'const node = "DeerFlowSummarizationMiddleware.before_model";',
      'throw new Error("Could not reach the DeerFlow backend.");',
      "const title = <h1>DeerFlow</h1>;",
    ].join("\n");
    expect(oldNameHits("src/core/x.tsx", app, APP_PRODUCT_NAME)).toEqual([
      'src/core/x.tsx:2 DeerFlow: "Could not reach the DeerFlow backend."',
      'src/core/x.tsx:3 DeerFlow: "DeerFlow"',
    ]);

    const css = "/* was oklch(0.9855 0.0098 87.47) */\n.dark { color: red; }";
    expect(hits("x.css", filePieces("x.css", css), RETIRED_STYLES)).toEqual([]);
    expect(
      hits(
        "x.css",
        filePieces("x.css", ".dark {\n  font-weight: 300;\n}"),
        RETIRED_STYLES,
      ),
    ).toEqual(["x.css:2 font-weight: 300"]);
  });
});
