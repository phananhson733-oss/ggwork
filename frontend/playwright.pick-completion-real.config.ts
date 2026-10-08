import { readFileSync } from "node:fs";

import { defineConfig, devices } from "@playwright/test";

const fixture = JSON.parse(
  readFileSync(process.env.PICK_COMPLETION_FIXTURE!, "utf8"),
);
if (
  !["source", "installed"].includes(fixture.mode) ||
  fixture.origin !== "synthetic_scripted" ||
  new URL(fixture.frontend_url).hostname !== "127.0.0.1"
)
  throw new Error("Isolated source/installed harness required");
export default defineConfig({
  testDir: "./tests/e2e-pick",
  testMatch:
    process.env.PICK_COMPLETION_PHASE === "review"
      ? "completion-review-real.spec.ts"
      : "completion-real.spec.ts",
  workers: 1,
  retries: 0,
  timeout: process.env.PICK_COMPLETION_VOICEOVER === "1" ? 420_000 : 240_000,
  expect: { timeout: 15_000 },
  outputDir: `${process.env.PICK_COMPLETION_OUTPUT}/artifacts`,
  reporter: [
    ["list"],
    [
      "html",
      {
        outputFolder: `${process.env.PICK_COMPLETION_OUTPUT}/html`,
        open: "never",
      },
    ],
    [
      "junit",
      { outputFile: `${process.env.PICK_COMPLETION_OUTPUT}/junit.xml` },
    ],
  ],
  use: {
    baseURL: fixture.frontend_url,
    actionTimeout: 15_000,
    ...devices["Desktop Chrome"],
    trace: "off",
    screenshot: "only-on-failure",
  },
});
