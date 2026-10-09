import { expect, test } from "@playwright/test";

import fixture from "../unit/core/pick/fixtures/completion-v1.json" with { type: "json" };

for (const legacy of [false, true]) {
  test(`real browser history: stay/discard/save, forward replay (${legacy ? "tracked fallback" : "native indices"})`, async ({
    page,
    context,
  }) => {
    if (legacy)
      await page.addInitScript(() =>
        Object.defineProperty(window, "navigation", {
          value: undefined,
          configurable: true,
        }),
      );
    let plan = structuredClone(fixture.plan);
    await context.addCookies([
      {
        name: "csrf_token",
        value: "fixture-csrf",
        url: "http://127.0.0.1:3097",
      },
    ]);
    await page.route("**/api/**", (route) => {
      const request = route.request();
      const path = new URL(request.url()).pathname;
      if (path.endsWith("/auth/me"))
        return route.fulfill({
          json: {
            id: "default",
            email: "history@test.local",
            system_role: "admin",
            needs_setup: false,
            oauth_provider: null,
          },
        });
      if (path.endsWith("/preferences"))
        return route.fulfill({ json: { preferences: {} } });
      if (path === `/api/pick/plans/${plan.id}`) {
        if (request.method() === "PATCH") {
          const body = request.postDataJSON();
          plan = {
            ...plan,
            title: body.title,
            rows: plan.rows.map((row, index) => ({
              ...row,
              ...body.rows[index],
            })),
            version: plan.version + 1,
          };
        }
        return route.fulfill({ json: plan });
      }
      if (path === "/api/pick/plans")
        return route.fulfill({
          json: { items: [plan], total: 1, next_offset: null },
        });
      if (path.endsWith("/notes"))
        return route.fulfill({ json: { item_facts: {} } });
      return route.fulfill({ json: {} });
    });
    await page.goto("/workspace/pick-plans");
    await page.getByRole("link", { name: plan.title, exact: true }).click();
    await page.getByLabel("计划名称", { exact: true }).fill("未保存后退");
    await page.evaluate(() => history.back());
    const dialog = page.getByRole("dialog", { name: "计划有未保存修改" });
    await expect(dialog).toBeVisible();
    await expect(page).toHaveURL(new RegExp(`/pick-plans/${plan.id}$`));
    await dialog.getByRole("button", { name: "留在本页" }).click();
    await expect(page.getByLabel("计划名称", { exact: true })).toHaveValue(
      "未保存后退",
    );
    await page.evaluate(() => history.back());
    await dialog.getByRole("button", { name: "丢弃修改并离开" }).click();
    await expect(page).toHaveURL(/\/pick-plans$/);
    await page.evaluate(() => history.forward());
    await expect(page.getByLabel("计划名称", { exact: true })).toHaveValue(
      plan.title,
    );
    // Create an actual forward entry through ordinary app navigation, then go back clean.
    await page.getByRole("link", { name: "全部排期", exact: true }).click();
    await expect(page).toHaveURL(/\/pick-plans$/);
    await page.goBack();
    await page.getByLabel("计划名称", { exact: true }).fill("保存并前进");
    await page.evaluate(() => history.forward());
    await expect(dialog).toBeVisible();
    await expect(page).toHaveURL(new RegExp(`/pick-plans/${plan.id}$`));
    await dialog.getByRole("button", { name: "保存并继续" }).click();
    await expect(page).toHaveURL(/\/pick-plans$/);
    await page.goBack();
    await expect(page.getByLabel("计划名称", { exact: true })).toHaveValue(
      "保存并前进",
    );
    const privacy = await page.evaluate(() => ({
      history: JSON.stringify(history.state),
      local: JSON.stringify(localStorage),
      session: JSON.stringify(sessionStorage),
    }));
    for (const value of Object.values(privacy))
      expect(value).not.toContain("保存并前进");
    expect(await page.evaluate(() => history.state.__NA)).toBe(true);
  });
}
