import { existsSync, statSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "@rstest/core";

import {
  FRONTEND_ROOT,
  readSource,
  stripComments,
} from "../../../core/pick-board/ported-source";

// The candidate card and the chat tool card are client components on the chat
// route. Following their runtime imports inside src/ must never reach the pick
// board's request.ts (502 lines) or metrics.ts: identity decoding needs only
// row-key.ts (critique B13). `import type` statements are erased; the inline
// `import { type X }` form is kept under verbatimModuleSyntax, so it counts.
const ENTRIES = [
  "src/components/workspace/pick/candidate-view.tsx",
  "src/components/workspace/pick/pick-tool-card.tsx",
];
const BANNED = [
  "src/core/pick-board/request.ts",
  "src/core/pick-board/metrics.ts",
];
const IMPORT =
  /^\s*(?:import|export)\s+(?!type\s)(?:[^;]*?\bfrom\s*)?["']([^"']+)["']/gm;

function isFile(relative: string): boolean {
  const absolute = path.join(FRONTEND_ROOT, relative);
  return existsSync(absolute) && statSync(absolute).isFile();
}

function resolveLocal(from: string, specifier: string): string | null {
  const base = specifier.startsWith("@/")
    ? path.posix.join("src", specifier.slice(2))
    : specifier.startsWith(".")
      ? path.posix.join(path.posix.dirname(from), specifier)
      : null;
  if (base === null) return null;
  const candidates = ["", ".ts", ".tsx", "/index.ts", "/index.tsx"].map(
    (suffix) => `${base}${suffix}`,
  );
  return candidates.find(isFile) ?? null;
}

function reachable(entry: string): ReadonlySet<string> {
  const seen = new Set<string>();
  const queue = [entry];
  while (queue.length > 0) {
    const file = queue.shift()!;
    if (seen.has(file) || !/\.(ts|tsx)$/.test(file)) continue;
    seen.add(file);
    const code = stripComments(readSource(file));
    for (const match of code.matchAll(IMPORT)) {
      const next = resolveLocal(file, match[1] ?? "");
      if (next !== null) queue.push(next);
    }
  }
  return seen;
}

describe("the chat route's pick cards stay small", () => {
  it.each(ENTRIES)("%s reaches row-key.ts but not request.ts", (entry) => {
    const files = reachable(entry);
    expect(files.has("src/core/pick/identity.ts")).toBe(true);
    expect(files.has("src/core/pick-board/row-key.ts")).toBe(true);
    for (const banned of BANNED) expect(files.has(banned)).toBe(false);
  });

  it("follows inline type imports but not import type statements", () => {
    const probe = [
      'import type { A } from "@/core/pick-board/request";',
      'import { type B } from "@/core/pick-board/row-key";',
    ].join("\n");
    const found = [...probe.matchAll(IMPORT)].map((match) => match[1]);
    expect(found).toEqual(["@/core/pick-board/row-key"]);
  });
});
