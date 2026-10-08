import { readFileSync } from "node:fs";

import { defineConfig, devices } from "@playwright/test";

const fixture = JSON.parse(
  readFileSync(process.env.PICK_COMPLETION_FIXTURE!, "utf8"),
);
if (
  fixture.mode !== "source" ||
  fixture.origin !== "synthetic_scripted" ||
  new URL(fixture.frontend_url).hostname !== "127.0.0.1"
)
  throw new Error("Isolated source harness required");
export default defineConfig({
  testDir: "./tests/e2e-pick",
  testMatch: "completion-real.spec.ts",
  workers: 1,
  retries: 0,
  timeout: 240_000,
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
  webServer: {
    command: `pnpm exec next dev --webpack --hostname 127.0.0.1 --port ${process.env.PICK_COMPLETION_FRONTEND_PORT}`,
    url: fixture.frontend_url,
    reuseExistingServer: false,
    timeout: 180_000,
  },
});
