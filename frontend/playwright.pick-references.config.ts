import { defineConfig, devices } from "@playwright/test";

// Synthetic HTTP only; the true authenticated runtime is covered separately.
export default defineConfig({
  testDir: "./tests/e2e-pick",
  testMatch: "plural-references.spec.ts",
  workers: 1,
  retries: 0,
  timeout: 90000,
  reporter: "list",
  use: {
    baseURL: "http://127.0.0.1:3104",
    ...devices["Desktop Chrome"],
    trace: "retain-on-failure",
  },
  webServer: {
    command:
      "DEER_FLOW_AUTH_DISABLED=1 SKIP_ENV_VALIDATION=1 pnpm exec next dev --webpack --hostname 127.0.0.1 --port 3104",
    url: "http://127.0.0.1:3104",
    reuseExistingServer: false,
    timeout: 120000,
  },
});
