import { readdirSync, readFileSync } from "node:fs";
import { join, relative } from "node:path";

import { describe, expect, it } from "@rstest/core";

import {
  DEMO_THREAD_IDS,
  resolveStaticDemoArtifact,
  STATIC_DEMO_ARTIFACTS,
} from "@/core/threads/static-demo";

function listFiles(root: string, directory = root): string[] {
  return readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const path = join(directory, entry.name);
    return entry.isDirectory()
      ? listFiles(root, path)
      : [relative(root, path).replaceAll("\\", "/")];
  });
}

describe("resolveStaticDemoArtifact", () => {
  const threadId = "7cfa5f8f-a2f8-47ad-acbd-da7137baf990";

  it("resolves a manifest-owned artifact", () => {
    expect(
      resolveStaticDemoArtifact(threadId, [
        "mnt",
        "user-data",
        "outputs",
        "index.html",
      ]),
    ).toBe(`/demo/threads/${threadId}/user-data/outputs/index.html`);
  });

  it.each([
    ["unknown", ["mnt", "user-data", "outputs", "index.html"]],
    ["constructor", ["mnt", "user-data", "outputs", "index.html"]],
    ["__proto__", ["mnt", "user-data", "outputs", "index.html"]],
    ["hasOwnProperty", ["mnt", "user-data", "outputs", "index.html"]],
    [threadId, ["mnt", "user-data", "outputs", "missing.txt"]],
    [threadId, ["mnt", "user-data", "outputs", "..", "thread.json"]],
    [threadId, ["mnt", "user-data", "outputs", "%2e%2e", "thread.json"]],
    [threadId, ["mnt", "user-data", "outputs%2F..%2Fthread.json"]],
  ])("rejects an unknown or unsafe path", (candidateThreadId, segments) => {
    expect(resolveStaticDemoArtifact(candidateThreadId, segments)).toBeNull();
  });

  it("keeps the deterministic manifest in sync with the checked-in fixtures", () => {
    const threadsRoot = join(
      import.meta.dirname,
      "../../../../public/demo/threads",
    );
    const fixtureThreadIds = readdirSync(threadsRoot, { withFileTypes: true })
      .filter((entry) => entry.isDirectory())
      .map((entry) => entry.name)
      .sort();

    expect([...DEMO_THREAD_IDS].sort()).toEqual(fixtureThreadIds);
    expect(Object.keys(STATIC_DEMO_ARTIFACTS).sort()).toEqual(fixtureThreadIds);

    for (const fixtureThreadId of fixtureThreadIds) {
      const fixtureFiles = listFiles(join(threadsRoot, fixtureThreadId))
        .filter((path) => path !== "thread.json")
        .sort();
      expect(
        [...(STATIC_DEMO_ARTIFACTS[fixtureThreadId] ?? [])].sort(),
      ).toEqual(fixtureFiles);
    }
  });
});

describe("demo fixtures", () => {
  // Demo HTML is served from this origin under /demo, so a remote script would
  // run with the workbench's storage and cookies when the file is opened
  // directly. Keep every script in the fixtures local and reviewable.
  it("load no remote scripts", () => {
    const threadsRoot = join(
      import.meta.dirname,
      "../../../../public/demo/threads",
    );
    const remoteScripts = listFiles(threadsRoot)
      .filter((path) => path.endsWith(".html"))
      .flatMap((path) =>
        Array.from(
          readFileSync(join(threadsRoot, path), "utf8").matchAll(
            /<script\b[^>]*\bsrc\s*=\s*["']?(?:https?:)?\/\/[^"'\s>]+/gi,
          ),
          (match) => `${path}: ${match[0]}`,
        ),
      );
    expect(remoteScripts).toEqual([]);
  });
});
