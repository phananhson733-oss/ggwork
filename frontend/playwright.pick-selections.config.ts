import { defineConfig, devices } from "@playwright/test";

import { localFixtureTarget } from "./tests/e2e-pick/support/readiness-protocol";

// Deterministic synthetic prior result against a real isolated Gateway. No model runs.
export default defineConfig({
  testDir: "./tests/e2e-pick",
  testMatch: "personal-selections-synthetic.spec.ts",
  workers: 1,
  retries: 0,
  timeout: 120_000,
  expect: { timeout: 20_000 },
  reporter: "list",
  use: {
    baseURL: localFixtureTarget(
      process.env.PICK_E2E_URL ?? "http://127.0.0.1:3076",
    ),
    trace: "off", // Login requests contain private fixture credentials.
    ...devices["Desktop Chrome"],
  },
});
