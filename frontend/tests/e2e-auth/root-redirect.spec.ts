import { expect, test } from "@playwright/test";

test("a signed-out visitor to / lands on the login page", async ({ page }) => {
  await page.goto("/");

  await expect(page).toHaveURL(/\/login(?:\?|$)/, { timeout: 15_000 });
});
