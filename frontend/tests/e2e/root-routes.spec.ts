import { expect, test } from "@playwright/test";

import { mockLangGraphAPI } from "./utils/mock-api";

// GGWork has no public landing page, docs or blog. This config runs with auth
// disabled, so "/" lands inside the app; tests/e2e-auth covers the signed-out
// path to /login.
test.describe("Root routes", () => {
  test("/ redirects into the workspace", async ({ page }) => {
    mockLangGraphAPI(page);

    const response = await page.request.get("/", { maxRedirects: 0 });
    expect(response.status()).toBe(307);
    expect(response.headers().location).toMatch(/\/workspace$/);

    await page.goto("/");
    await page.waitForURL("**/workspace/chats/new");
  });

  for (const path of ["/en/docs", "/zh/docs", "/blog/posts", "/github-stars"]) {
    test(`retired marketing route ${path} is gone`, async ({ page }) => {
      const response = await page.request.get(path, { maxRedirects: 0 });
      expect(response.status()).toBe(404);
    });
  }

  test("every response asks search engines not to index it", async ({
    page,
  }) => {
    for (const path of [
      "/",
      "/workspace/chats/new",
      "/images/ggwork-logo.png",
    ]) {
      const response = await page.request.get(path, { maxRedirects: 0 });
      expect(response.headers()["x-robots-tag"], path).toBe(
        "noindex, nofollow",
      );
    }

    await page.goto("/workspace/chats/new");
    await expect(page.locator('meta[name="robots"]')).toHaveAttribute(
      "content",
      "noindex, nofollow",
    );
  });
});
