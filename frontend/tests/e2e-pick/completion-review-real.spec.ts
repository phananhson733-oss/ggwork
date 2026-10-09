import { randomUUID } from "node:crypto";
import { readFileSync, writeFileSync } from "node:fs";

import { expect, test } from "@playwright/test";

import type { ReviewPost } from "@/core/pick/completion-types";

import { visualMeasurements } from "./support/completion-visual";

// Reads the actual privately published synthetic feedback snapshot. No Lark call,
// provider run, response fulfillment, or automatic identity matching is used.
test("real Gateway: actual post metrics and explicit manual plan association", async ({
  page,
  context,
  browser,
}, info) => {
  const fixture = JSON.parse(
    readFileSync(process.env.PICK_COMPLETION_FIXTURE!, "utf8"),
  );
  expect(fixture.origin).toBe("synthetic_scripted");
  expect(fixture.review.plan_id).toMatch(/^[a-f0-9]{32}$/);
  const denied: string[] = [];
  await context.route("**/*", async (route) => {
    const url = new URL(route.request().url());
    if (
      url.hostname !== "127.0.0.1" ||
      (/\/runs(?:\/stream)?$/.test(url.pathname) &&
        route.request().method() === "POST") ||
      url.pathname.endsWith("/feedback/sync")
    ) {
      denied.push(url.pathname);
      return route.abort();
    }
    return route.continue();
  });
  expect(
    (
      await context.request.post("/api/v1/auth/login/local", {
        form: { username: fixture.email, password: fixture.password },
      })
    ).ok(),
  ).toBe(true);
  expect(
    (await (await context.request.get("/api/v1/auth/me")).json()).system_role,
  ).toBe("user");
  const response = await context.request.get("/api/pick/feedback/posts");
  expect(response.ok()).toBe(true);
  const posts = (await response.json()) as {
    total: number;
    feedback_version_id: string;
    items: ReviewPost[];
  };
  expect(posts.total).toBe(3);
  expect(posts.feedback_version_id).toBe(fixture.review.feedback_version_id);
  expect(posts.items.map((post) => post.views)).toEqual([150, null, 0]);
  expect(
    posts.items.every(
      (post) =>
        post.link === null &&
        !post.window_complete &&
        post.revenue.length === 0,
    ),
  ).toBe(true);
  await page.goto("/workspace/pick-review");
  await expect(
    page.getByRole("heading", { name: "发布复盘", exact: true }),
  ).toBeVisible();
  await expect(page.getByText("播放：150", { exact: true })).toBeVisible();
  await expect(
    page.getByText("播放：未提供/未更新", { exact: true }),
  ).toBeVisible();
  await expect(page.getByText("播放：0", { exact: true })).toBeVisible();
  await expect(page.getByText("未关联计划", { exact: true })).toHaveCount(3);
  await expect(
    page.getByText("收益未提供/未更新", { exact: true }),
  ).toHaveCount(3);
  await expect(page.getByText(/观察时长或指标不足/)).toHaveCount(3);
  await page
    .getByRole("button", { name: "人工关联计划行", exact: true })
    .first()
    .click();
  await page.getByLabel("选择个人计划").selectOption(fixture.review.plan_id);
  await page.getByLabel("选择计划行").selectOption(fixture.review.row_id);
  const confirm = page.getByRole("button", {
    name: "确认双方证据并关联",
    exact: true,
  });
  await expect(confirm).toBeDisabled();
  await expect(
    page.getByRole("region", { name: "人工关联核对" }),
  ).toContainText("系统尚未验证两者对应关系");
  await page
    .getByRole("checkbox", {
      name: "我已核对两侧身份、账号、时间与来源证据，确认关联",
    })
    .check();
  const linkResponse = page.waitForResponse(
    (result) =>
      result.url().endsWith("/feedback/plan-links") &&
      result.request().method() === "POST",
  );
  await confirm.click();
  const linked = await linkResponse;
  expect(linked.ok()).toBe(true);
  const receipt = await linked.json();
  expect(receipt.method).toBe("manual");
  expect(receipt.plan_id).toBe(fixture.review.plan_id);
  expect(receipt.row_id).toBe(fixture.review.row_id);
  expect(receipt.feedback_version_id).toBe(fixture.review.feedback_version_id);
  await expect(
    page.getByText("关联已确认；这不执行发帖。", { exact: true }),
  ).toBeVisible();
  await page.reload();
  await expect(
    page.getByText("已确认关联 · 人工核对", { exact: true }),
  ).toBeVisible();
  await expect(page.getByText("未关联计划", { exact: true })).toHaveCount(2);
  let followUpQueries = 0;
  page.on("request", (request) => {
    if (new URL(request.url()).pathname === "/api/pick/query")
      followUpQueries++;
  });
  await page
    .getByRole("button", { name: "继续选剧（发起新查询）", exact: true })
    .first()
    .click();
  await expect(
    page.getByRole("heading", { name: "同账号继续选剧暂不可用", exact: true }),
  ).toBeVisible();
  expect(followUpQueries).toBe(0);
  const reviewMeasurements: {
    theme: string;
    width: number;
    scope: string;
    measurement: Awaited<ReturnType<typeof visualMeasurements>>;
  }[] = [];
  for (const theme of ["light", "dark"]) {
    await page.evaluate((value) => {
      localStorage.setItem("theme", value);
      document.documentElement.classList.toggle("dark", value === "dark");
    }, theme);
    for (const width of [320, 768, 1280, 1440]) {
      await page.setViewportSize({ width, height: 900 });
      await expect
        .poll(() =>
          page.evaluate(
            () => document.documentElement.scrollWidth <= innerWidth,
          ),
        )
        .toBe(true);
      await page.screenshot({
        path: info.outputPath(`review-${theme}-${width}.png`),
        fullPage: true,
        animations: "disabled",
      });
      reviewMeasurements.push({
        theme,
        width,
        scope: "published review business main",
        measurement: await visualMeasurements(page, "main"),
      });
    }
  }
  const other = await browser.newContext({ baseURL: fixture.frontend_url });
  try {
    expect(
      (
        await other.request.post("/api/v1/auth/register", {
          data: {
            email: "qa-review-other@example.com",
            password: "Qa9!" + randomUUID(),
          },
        })
      ).status(),
    ).toBe(201);
    const hidden = await (
      await other.request.get("/api/pick/feedback/posts")
    ).json();
    expect(hidden.status).toBe("auth_required");
    expect(hidden.total).toBeNull();
    expect(hidden.items).toEqual([]);
    expect(
      (
        await other.request.get(`/api/pick/plans/${fixture.review.plan_id}`)
      ).status(),
    ).toBe(404);
  } finally {
    await other.close();
  }
  writeFileSync(
    info.outputPath("review-measurements.json"),
    JSON.stringify(reviewMeasurements, null, 2),
  );
  expect(denied).toEqual([]);
  writeFileSync(
    info.outputPath("review-evidence.json"),
    JSON.stringify(
      {
        real_gateway: true,
        private_synthetic_source: true,
        ordinary_cookie: true,
        provider_calls: 0,
        lark_calls: 0,
        views: posts.items.map((post) => post.views),
        receipt,
        actual_screen_reader:
          "not verified; separate native observation retained",
      },
      null,
      2,
    ),
  );
});
