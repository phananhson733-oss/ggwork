import { existsSync, readdirSync, readFileSync } from "node:fs";
import path from "node:path";

/**
 * Source-shape tests ported from RealShort read files that later batches land:
 * queries (P3-3) under src/server/pick-board, components (P3-4) under
 * src/components/workspace/pick-board, views (P3-5) under its views/ folder.
 * Each block runs once its batch's anchor exists; from then on a missing file
 * throws instead of skipping, so a rename cannot silently turn a check off.
 */
export const FRONTEND_ROOT = path.resolve(__dirname, "../../../..");
export const REPO_ROOT = path.resolve(FRONTEND_ROOT, "..");

export const COMPONENTS_DIR = "src/components/workspace/pick-board";
export const VIEWS_DIR = `${COMPONENTS_DIR}/views`;
export const QUERIES_FILE = "src/server/pick-board/queries.ts";
export const QUERIES_RANK_FILE = "src/server/pick-board/queries-rank.ts";
export const PAGE_FILE = "src/app/workspace/pick-data/page.tsx";

function exists(relativePath: string): boolean {
  return existsSync(path.join(FRONTEND_ROOT, relativePath));
}

export const LANDED = Object.freeze({
  components: exists(COMPONENTS_DIR),
  queries: exists(QUERIES_FILE),
  views: exists(VIEWS_DIR),
});

export function readSource(relativePath: string): string {
  return readFileSync(path.join(FRONTEND_ROOT, relativePath), "utf8");
}

/** The .tsx files directly inside a folder (not its subfolders), as frontend-relative paths. */
export function tsxIn(relativeDir: string): string[] {
  return readdirSync(path.join(FRONTEND_ROOT, relativeDir))
    .filter((name) => name.endsWith(".tsx"))
    .map((name) => `${relativeDir}/${name}`);
}

/** Block and line comments removed, as RealShort's stripComments. */
export function stripComments(source: string): string {
  return source.replace(/\/\*[\s\S]*?\*\/|\/\/[^\n]*/g, "");
}
