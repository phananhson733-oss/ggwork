import { readdirSync, readFileSync, statSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "@rstest/core";

/**
 * src/server/pick-board/PORTED_FROM is what the drift script diffs RealShort
 * against: the first line names the commit, every other line one RealShort
 * path. Every PORTED_FROM header in the frontend must be covered by it.
 */

const ROOT = path.resolve(__dirname, "../../../..");
const LIST = path.join(ROOT, "src/server/pick-board/PORTED_FROM");

function readList(): string[] {
  return readFileSync(LIST, "utf8").split("\n");
}

function walk(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const child = path.join(dir, name);
    if (statSync(child).isDirectory()) return walk(child);
    return /\.(ts|tsx)$/.test(name) ? [child] : [];
  });
}

/** The RealShort paths named on `// PORTED_FROM: realshort@816ca2e ...` lines */
function headerPaths(): string[] {
  const header = /^\/\/ PORTED_FROM: realshort@816ca2e (.*)$/gm;
  const rsPath = /\b(?:src|tests)\/[A-Za-z0-9_./()-]+\.tsx?/g;
  return [...walk(path.join(ROOT, "src")), ...walk(path.join(ROOT, "tests"))]
    .flatMap((file) =>
      [...readFileSync(file, "utf8").matchAll(header)].map((m) => m[1] ?? ""),
    )
    .flatMap((line) => line.match(rsPath) ?? []);
}

describe("PORTED_FROM", () => {
  it("starts with the source commit and then lists one relative RealShort path per line", () => {
    const [first, ...rest] = readList();
    expect(first).toBe("commit 816ca2e");
    const paths = rest.filter((line) => line !== "");
    expect(rest.slice(0, -1)).not.toContain("");
    for (const line of paths)
      expect(line).toMatch(/^(?:src|tests)\/[A-Za-z0-9_./()[\]-]+\.tsx?$/);
    expect(new Set(paths).size).toBe(paths.length);
  });

  it("covers every PORTED_FROM header in src and tests", () => {
    const listed = new Set(readList().slice(1));
    const headers = [...new Set(headerPaths())];
    expect(headers.length).toBeGreaterThanOrEqual(20);
    expect(headers.filter((p) => !listed.has(p))).toEqual([]);
  });

  it("names the semantic sources the mirror queries were rewritten from", () => {
    const listed = readList();
    for (const source of [
      "src/lib/observe/queries.ts",
      "src/lib/observe/metrics.ts",
      "src/lib/queries.ts",
      "src/lib/pick/export-v2.ts",
      "src/lib/pick/export-v2-map.ts",
    ])
      expect(listed).toContain(source);
  });
});
