/**
 * scripts/pick-board-drift.sh (P4-3) against a throwaway git repository that
 * stands in for a RealShort checkout: it diffs the PORTED_FROM paths between
 * the listed commit and origin/main and exits 1 when anything changed.
 */
import { spawnSync, type SpawnSyncReturns } from "node:child_process";
import {
  mkdirSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";

import { afterAll, beforeAll, describe, expect, it } from "@rstest/core";

const FRONTEND = path.resolve(__dirname, "../../..");
const SCRIPT = path.join(FRONTEND, "scripts/pick-board-drift.sh");
const PAGE = "src/app/admin/(protected)/pick/page.tsx";
const BRACKETED = "src/app/api/[resource]/route.ts";
const PORTED = ["src/lib/pick/queries.ts", PAGE, BRACKETED];

let repo = "";
let list = "";
let base = "";

function git(...args: string[]): string {
  const result = spawnSync(
    "git",
    [
      "-c",
      "user.name=drift-test",
      "-c",
      "user.email=drift-test@example.invalid",
      "-c",
      "commit.gpgsign=false",
      "-C",
      repo,
      ...args,
    ],
    { encoding: "utf8", timeout: 10_000 },
  );
  if (result.status !== 0) throw new Error(result.stderr);
  return result.stdout.trim();
}

function write(file: string, text: string): void {
  mkdirSync(path.dirname(path.join(repo, file)), { recursive: true });
  writeFileSync(path.join(repo, file), text);
}

function commit(message: string): string {
  git("add", "-A");
  git("commit", "-q", "-m", message);
  return git("rev-parse", "HEAD");
}

function mainAt(sha: string): void {
  git("update-ref", "refs/remotes/origin/main", sha);
}

function drift(
  env: Record<string, string | undefined>,
): SpawnSyncReturns<string> {
  return spawnSync("bash", [SCRIPT], {
    encoding: "utf8",
    timeout: 20_000,
    // Only what the test passes: an RS_REPO or PORTED_FROM of the outer
    // shell must not leak in (spawn drops undefined values).
    env: { ...process.env, RS_REPO: env.RS_REPO, PORTED_FROM: env.PORTED_FROM },
  });
}

function writeList(first: string, paths: readonly string[]): void {
  writeFileSync(list, [first, ...paths, ""].join("\n"));
}

beforeAll(() => {
  const dir = mkdtempSync(path.join(tmpdir(), "pick-drift-"));
  repo = path.join(dir, "rs (checkout)");
  list = path.join(dir, "PORTED_FROM");
  mkdirSync(repo);
  git("init", "-q");
  for (const file of [...PORTED, "src/lib/x/route.ts", "README.md"])
    write(file, `${file}\n`);
  base = commit("base");
  mainAt(base);
  writeList(`commit ${base.slice(0, 7)}`, PORTED);
});

afterAll(() => {
  if (repo) rmSync(path.dirname(repo), { recursive: true, force: true });
});

describe("pick-board-drift.sh", () => {
  it("prints nothing and exits 0 while origin/main has not touched the ported files", () => {
    write("README.md", "changed\n");
    mainAt(commit("unrelated"));
    const run = drift({ RS_REPO: repo, PORTED_FROM: list });
    expect([run.status, run.stdout]).toEqual([0, ""]);
  });

  it("exits 1 with a diff stat naming the file when a ported file changed", () => {
    write(PAGE, "changed\n");
    mainAt(commit("page"));
    const run = drift({ RS_REPO: repo, PORTED_FROM: list });
    expect(run.status).toBe(1);
    expect(run.stdout).toContain("(protected)/pick/page.tsx");
    git("reset", "-q", "--hard", base);
    mainAt(base);
  });

  it("diffs against origin/main, not against the checked-out HEAD", () => {
    // origin/main moved, the checkout stayed at the base: a drift.
    write(PAGE, "changed upstream\n");
    mainAt(commit("upstream"));
    git("reset", "-q", "--hard", base);
    const upstream = drift({ RS_REPO: repo, PORTED_FROM: list });
    expect(upstream.status).toBe(1);
    expect(upstream.stdout).toContain("(protected)/pick/page.tsx");
    // A local commit (a feature branch, a detached sourceRevision) is not.
    mainAt(base);
    write(PAGE, "changed locally\n");
    commit("local");
    const local = drift({ RS_REPO: repo, PORTED_FROM: list });
    expect([local.status, local.stdout]).toEqual([0, ""]);
    git("reset", "-q", "--hard", base);
  });

  it("takes the listed paths literally: [resource] is not a character class", () => {
    write("src/app/api/r/route.ts", "decoy\n");
    mainAt(commit("decoy"));
    const decoy = drift({ RS_REPO: repo, PORTED_FROM: list });
    expect([decoy.status, decoy.stdout]).toEqual([0, ""]);
    write(BRACKETED, "changed\n");
    mainAt(commit("bracketed"));
    const run = drift({ RS_REPO: repo, PORTED_FROM: list });
    expect(run.status).toBe(1);
    expect(run.stdout).toContain("[resource]/route.ts");
    git("reset", "-q", "--hard", base);
    mainAt(base);
  });

  it("exits 2 without RS_REPO, with a bad first line, or with an unknown commit", () => {
    expect(drift({ PORTED_FROM: list }).status).toBe(2);
    writeList("commit not-a-sha", PORTED);
    expect(drift({ RS_REPO: repo, PORTED_FROM: list }).status).toBe(2);
    writeList("commit 0123456", PORTED);
    const unknown = drift({ RS_REPO: repo, PORTED_FROM: list });
    expect(unknown.status).toBe(2);
    expect(unknown.stderr).toContain("0123456");
    writeList(`commit ${base.slice(0, 7)}`, []);
    expect(drift({ RS_REPO: repo, PORTED_FROM: list }).status).toBe(2);
    writeList(`commit ${base.slice(0, 7)}`, PORTED);
  });

  it("exits 2 when origin/main is missing: it never fetches on its own", () => {
    git("update-ref", "-d", "refs/remotes/origin/main");
    const run = drift({ RS_REPO: repo, PORTED_FROM: list });
    expect(run.status).toBe(2);
    expect(run.stderr).toContain("origin/main");
    mainAt(base);
    expect(readFileSync(SCRIPT, "utf8")).not.toMatch(/\bgit\b[^\n]*\bfetch\b/);
  });

  it("reads src/server/pick-board/PORTED_FROM by default", () => {
    const run = drift({ RS_REPO: repo });
    expect(run.status).toBe(2);
    expect(run.stderr).toContain("816ca2e");
  });
});
