import { defineConfig, devices } from "@playwright/test";

const baseURL = process.env.PICK_FEEDBACK_E2E_URL;
if (
  !baseURL ||
  !["127.0.0.1", "localhost"].includes(new URL(baseURL).hostname)
) {
  throw new Error(
    "PICK_FEEDBACK_E2E_URL must identify an isolated localhost fixture",
  );
}
export default defineConfig({
  testDir: "./tests/e2e-pick",
  testMatch: "pick-feedback.spec.ts",
  workers: 1,
  fullyParallel: false,
  retries: 0,
  timeout: 180_000,
  reporter: [
    ["list"],
    ["html", { outputFolder: "test-results/feedback/report", open: "never" }],
  ],
  outputDir: "test-results/feedback/artifacts",
  use: {
    ...devices["Desktop Chrome"],
    baseURL,
    trace: "off",
    video: "off",
    screenshot: "off",
  },
});
