import { defineConfig, devices } from "@playwright/test";

// Requires an isolated, authenticated pick instance and a real configured model.
// Remote acceptance requires explicit opt-in and an isolated regular QA account.
export default defineConfig({
  testDir: "./tests/e2e-pick",
  testMatch: ["personal-selection.spec.ts", "pick-data-board.spec.ts"],
  fullyParallel: false,
  workers: 1,
  retries: 0,
  // Includes cold Next compilation, authentication, model run and persistence.
  // The service retains its separate execution budget.
  timeout: (Number(process.env.PICK_E2E_RUN_TIMEOUT_SECONDS ?? 600) + 60) * 1000 + 120_000,
  reporter: "list",
  use: {
    baseURL: process.env.PICK_E2E_URL ?? "http://localhost:3008",
    trace: "retain-on-failure",
    ...devices["Desktop Chrome"],
  },
});
