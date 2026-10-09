import { defineConfig } from "@playwright/test";

import base from "./playwright.config";

export default defineConfig({
  ...base,
  testDir: "./tests/e2e-editing",
  fullyParallel: false,
  workers: 1,
  timeout: 60_000,
  reporter: "list",
});
