import { defineConfig, devices } from "@playwright/test";

// Real browser/Next history, synthetic mocked HTTP; no model or backend acceptance claim.
export default defineConfig({
  testDir: "./tests/e2e-pick",
  testMatch: "plan-final-fixes.spec.ts",
  workers: 1,
  retries: 0,
  timeout: 90000,
  use: {
    baseURL: "http://127.0.0.1:3097",
    ...devices["Desktop Chrome"],
    trace: "retain-on-failure",
  },
  webServer: {
    command:
      "DEER_FLOW_AUTH_DISABLED=1 SKIP_ENV_VALIDATION=1 pnpm exec next dev --webpack --hostname 127.0.0.1 --port 3097",
    url: "http://127.0.0.1:3097",
    reuseExistingServer: false,
    timeout: 120000,
  },
});
