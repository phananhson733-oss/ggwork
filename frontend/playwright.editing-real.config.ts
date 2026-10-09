import { defineConfig, devices } from "@playwright/test";

if (!process.env.PLAYWRIGHT_BASE_URL || !process.env.EDITING_QA_CONFIG)
  throw new Error(
    "Provide a dedicated frontend URL and private EDITING_QA_CONFIG file",
  );
if (
  !["localhost", "127.0.0.1", "[::1]"].includes(
    new URL(process.env.PLAYWRIGHT_BASE_URL).hostname,
  )
)
  throw new Error(
    "Native acceptance is restricted to an isolated loopback frontend",
  );

export default defineConfig({
  testDir: "./tests/e2e-editing-real",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 300_000,
  outputDir: process.env.EDITING_EVIDENCE_DIR ?? "test-results/editing-real",
  reporter: "list",
  use: {
    baseURL: process.env.PLAYWRIGHT_BASE_URL,
    actionTimeout: 30_000,
    // Auth cookies and native connection tokens must not enter trace archives.
    trace: "off",
    screenshot: "off",
    video: "off",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
