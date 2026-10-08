import { expect, test } from "@playwright/test";

import fixture from "../unit/core/pick/fixtures/completion-v1.json" with { type: "json" };

test("mock HTTP: plan edits, blockers, immutable receipt and review across responsive widths", async ({
  page,
  context,
}, info) => {
  let plan = structuredClone(fixture.plan);
  let exported = 0;
  let blocked = true;
  let downloads = 0;

  await context.addCookies([
    { name: "csrf_token", value: "fixture-csrf", url: "http://127.0.0.1:3092" },
  ]);
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path.endsWith("/auth/me"))
      return route.fulfill({
        json: {
          id: "default",
          email: "default@test.local",
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
        expect(request.headers()["x-csrf-token"]).toBe("fixture-csrf");
        plan = {
          ...plan,
          ...body,
          rows: plan.rows.map((row, index) => ({
            ...row,
            ...body.rows[index],
          })),
          version: plan.version + 1,
        };
        delete (plan as Record<string, unknown>).request_id;
        delete (plan as Record<string, unknown>).expected_version;
        delete (plan as Record<string, unknown>).timezone_change;
      }
      return route.fulfill({ json: plan });
    }
    if (path.endsWith("/preview"))
      return route.fulfill({
        json: {
          ...fixture.preview,
          plan,
          checks: plan.rows.map((row) => ({
            row_id: row.row_id,
            status: blocked ? "blocked" : "ready",
            blockers: blocked ? ["必要渠道来源暂不可用"] : [],
            warnings: [],
            current_pin: blocked ? null : row.source_pin,
          })),
          exportable: !blocked,
        },
      });
    if (path === "/api/pick/feedback/posts")
      return route.fulfill({ json: fixture.review });
    if (path === "/api/pick/plans")
      return route.fulfill({
        json: { items: [plan], total: 1, next_offset: null },
      });
    if (path === `/api/pick/plans/${plan.id}/exports`) {
      exported++;
      return route.fulfill({
        json: {
          ...fixture.export,
          plan_id: plan.id,
          plan_version: plan.version,
          preview_id: fixture.preview.preview_id,
        },
      });
    }
    if (path === `/api/pick/exports/${fixture.export.id}`) {
      downloads++;
      if (downloads === 1) return route.abort("failed");
      return route.fulfill({
        contentType: "text/csv; charset=utf-8",
        body: "\uFEFFplan_id,plan_version\r\nfixture,2\r\n",
      });
    }
    if (path === "/api/pick/feedback/plan-links")
      return route.fulfill({
        json: { ...fixture.link, plan_id: plan.id, plan_version: plan.version },
      });
    return route.fulfill({
      status: 503,
      json: { detail: "not part of frontend fixture" },
    });
  });
  await page.goto(`/workspace/pick-plans/${plan.id}`);
  await expect(
    page.getByRole("heading", { name: "排期草稿", exact: true }),
  ).toBeVisible();
  await page
    .getByLabel("计划名称", { exact: true })
    .fill("长标题排期：English 中文来源身份与个人备注");
  await page.getByRole("button", { name: "保存计划", exact: true }).click();
  await expect(page.getByRole("status")).toContainText("已保存版本");
  await page.getByRole("button", { name: "核对执行条件" }).click();
  await expect(page.getByText("必要渠道来源暂不可用")).toBeVisible();
  await expect(page.getByRole("button", { name: /生成执行表/ })).toHaveCount(0);
  for (const width of [320, 768, 1280, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    await expect
      .poll(() =>
        page.evaluate(
          () => document.documentElement.scrollWidth <= window.innerWidth,
        ),
      )
      .toBe(true);
    await page.screenshot({
      path: info.outputPath(`plan-${width}.png`),
      fullPage: true,
    });
  }
  await page.setViewportSize({ width: 320, height: 900 });
  await page.getByRole("button", { name: /编辑 / }).first().click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await expect(page.getByLabel("发布账号", { exact: true })).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: /编辑 / }).first(),
  ).toBeFocused();
  expect(exported).toBe(0);
  blocked = false;
  await page.getByRole("button", { name: "核对执行条件" }).click();
  await page.getByRole("button", { name: /确认版本.*生成执行表/ }).click();
  await page.getByRole("button", { name: "下载同一执行表" }).click();
  await expect(
    page.getByText("操作结果尚未确认，请保留修改并重试原操作。", {
      exact: true,
    }),
  ).toBeVisible();
  const download = page.waitForEvent("download");
  await page.getByRole("button", { name: "下载同一执行表" }).click();
  expect((await download).suggestedFilename()).toBe(fixture.export.filename);
  expect(exported).toBe(1);
  expect(downloads).toBe(2);
  await page.evaluate(() => document.documentElement.classList.add("dark"));
  await page.screenshot({
    path: info.outputPath("plan-dark-320.png"),
    fullPage: true,
  });
  await page.goto("/workspace/pick-review");
  await expect(
    page.getByRole("heading", { name: "发布复盘", exact: true }),
  ).toBeVisible();
  await expect(page.getByText(/已观察/)).toBeVisible();
  await page.screenshot({
    path: info.outputPath("review-320.png"),
    fullPage: true,
  });
  await page.getByRole("button", { name: "人工关联计划行" }).click();
  await page.getByLabel("选择个人计划").selectOption(plan.id);
  await page.getByLabel("选择计划行").selectOption(plan.rows[0]!.row_id);
  await expect(
    page.getByRole("button", { name: "确认双方证据并关联" }),
  ).toBeDisabled();
  await page.getByRole("checkbox", { name: /我已核对/ }).check();
  await page.getByRole("button", { name: "确认双方证据并关联" }).click();
  await expect(page.getByRole("status")).toContainText("关联已确认");
});
