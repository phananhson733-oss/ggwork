import { defineConfig, devices } from "@playwright/test";

import {
  budgets,
  loadManifest,
} from "./tests/e2e-pick/support/readiness-protocol";

const { manifest } = loadManifest(process.env.PICK_READINESS_MANIFEST);

export default defineConfig({
  testDir: "./tests/e2e-pick",
  testMatch: "readiness.spec.ts",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: Math.max(
    ...manifest.cases.map(
      (item: { planned_max_runs: number }) =>
        budgets(manifest.runtime.run_timeout_seconds, item.planned_max_runs)
          .caseTimeoutMs,
    ),
  ),
  outputDir: `${manifest.runner.evidence_dir}/playwright`,
  reporter: "list",
  use: {
    baseURL: manifest.runner.target_url,
    // Auth requests/cookies must never enter an unredacted trace or CI artifact.
    trace: "off",
    screenshot: "off",
    video: "off",
    ...devices["Desktop Chrome"],
  },
});
