import { expect, test, type Page } from "@playwright/test";

import { mockLangGraphAPI } from "../e2e/utils/mock-api";

// Fixture browser checks exercise the real frontend, not native/backend acceptance.
const title = "雨夜重逢与命运交错的漫长故事".repeat(4);
const device = {
  id: "mac",
  name: "Studio Mac",
  online: true,
  ready: true,
  revoked: false,
  reasons: [],
  grants: ["drama"],
  platform: "darwin-arm64",
  worker_version: "fixture",
};
const capability = {
  profiles: [{ id: "hook", available: true, reasons: [] }],
  skill_enabled: true,
  limits: {
    max_sources: 50,
    max_outputs: 4,
    min_duration_seconds: 5,
    max_duration_seconds: 90,
  },
};
async function setup(page: Page) {
  mockLangGraphAPI(page);
  await page.route("**/api/editing/**", (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/devices"))
      return route.fulfill({ json: { items: [device] } });
    if (path.endsWith("/capabilities"))
      return route.fulfill({ json: capability });
    return route.fulfill({ status: 404, json: { detail: "not_found" } });
  });
}
for (const width of [375, 768, 1440]) {
  test(`fixture create form remains readable and operable at ${width}px`, async ({
    page,
  }, info) => {
    await page.setViewportSize({ width, height: 1000 });
    await setup(page);
    await page.goto(`/workspace/editing/new?${new URLSearchParams({ title })}`);
    const main = page.locator("main.editing-workspace");
    await expect(main.getByRole("heading", { name: "新建剪辑" })).toBeVisible();
    await page.getByLabel("选择执行设备").selectOption("mac");
    await page
      .getByRole("combobox", { name: "已授权目录", exact: true })
      .selectOption("drama");
    await page
      .getByRole("textbox", { name: "剪辑要求", exact: true })
      .fill("保留人物冲突与完整对白");
    await expect(page.getByLabel("当前剧目")).toHaveValue(title);
    const metrics = await main.evaluate((root) => ({
      overflow: root.scrollWidth > root.clientWidth,
      controls: Array.from(
        root.querySelectorAll(
          "button, input:not([type=checkbox]), select, textarea, a, summary",
        ),
      )
        .filter((el) => el.getClientRects().length > 0)
        .map((el) => ({
          text: el.getAttribute("aria-label") ?? el.textContent?.trim(),
          height: el.getBoundingClientRect().height,
          font: parseFloat(getComputedStyle(el).fontSize),
        })),
    }));
    expect(metrics.overflow).toBe(false);
    expect(
      metrics.controls.filter(
        (control) => control.height < 44 || control.font < 16,
      ),
    ).toEqual([]);
    await page.getByRole("textbox", { name: "剪辑要求", exact: true }).focus();
    await page.keyboard.press("Tab");
    await expect(page.getByLabel("成片数量")).toBeFocused();
    await main.screenshot({ path: info.outputPath(`create-${width}.png`) });
  });
}

const task = {
  id: "fixture-task",
  title,
  requirements: {
    profile: "hook",
    instructions: "保持原版要求与对白",
    output_count: 3,
    duration_seconds: 30,
    aspect_ratio: "9:16",
    language: "auto",
    review_plan: false,
  },
  device_id: "mac",
  source_thread_id: null,
  parent_task_id: null,
  created_at: "2026-10-08T09:00:00Z",
  updated_at: "2026-10-08T09:00:00Z",
  status: "partial",
  stage: "rendering",
  result: "partial",
  source_manifest: null,
  source_directory: null,
  manifest_frozen: true,
  requested_count: 3,
  completed_count: 2,
  preparation_reasons: [],
  device_status: "offline",
  access_status: "device_offline",
  available_actions: ["retry"],
  plan_confirmed: true,
  plan: null,
  outputs: [1, 2, 3].map((n) => ({
    id: `out-${n}`,
    status: n === 3 ? "failed" : "completed",
    error: n === 3 ? "render_failed" : null,
    access_status: "device_offline",
    result:
      n === 3
        ? null
        : {
            verified: true,
            artifact_id: `a-${n}`,
            size_bytes: 1000,
            duration_seconds: 30,
          },
  })),
};
test("fixture task preserves partial delivery offline at 200% zoom in light and dark themes", async ({
  page,
}, info) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  await setup(page);
  await page.route("**/api/editing/tasks/fixture-task", (route) =>
    route.fulfill({ json: task }),
  );
  const pageErrors: string[] = [];
  page.on("pageerror", (error) => pageErrors.push(error.message));
  await page.goto("/workspace/editing/fixture-task");
  const main = page.locator("main.editing-workspace");
  await expect(main.getByRole("status")).toContainText("部分完成");
  await expect(main.getByRole("status")).toContainText("2/3 条");
  await expect(main.getByRole("link", { name: "下载成片" })).toHaveCount(0);
  await expect(
    main.getByText("视频渲染失败，请检查 Mac 环境与可用磁盘空间后重试此条"),
  ).toBeVisible();
  await page.evaluate(() => {
    document.documentElement.style.zoom = "2";
  });
  for (const theme of ["light", "dark"]) {
    await page.evaluate(
      (value) =>
        document.documentElement.classList.toggle("dark", value === "dark"),
      theme,
    );
    expect(
      await main.evaluate((root) => root.scrollWidth <= root.clientWidth),
    ).toBe(true);
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth,
      ),
    ).toBe(true);
    const contrast = await main
      .getByRole("status")
      .locator("span")
      .evaluate((el) => {
        const style = getComputedStyle(el);
        const luminance = (color: string) => {
          const channels = color
            .match(/[\d.]+/g)!
            .slice(0, 3)
            .map((value) => {
              const s = Number(value) / 255;
              return s <= 0.04045 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
            });
          return (
            channels[0]! * 0.2126 +
            channels[1]! * 0.7152 +
            channels[2]! * 0.0722
          );
        };
        const a = luminance(style.color),
          b = luminance(style.backgroundColor);
        return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
      });
    expect(contrast).toBeGreaterThanOrEqual(4.5);
    await main.screenshot({
      path: info.outputPath(`partial-zoom-${theme}.png`),
    });
  }
  expect(pageErrors).toEqual([]);
});

test("fixture history read failure stays distinct from an empty history", async ({
  page,
}) => {
  await setup(page);
  await page.route("**/api/editing/tasks?**", (route) =>
    route.fulfill({ status: 503, json: { detail: "server_private_path" } }),
  );
  await page.goto("/workspace/editing");
  const main = page.locator("main.editing-workspace");
  await expect(main.getByRole("alert")).toContainText("任务暂时未加载");
  await expect(main.getByText(/还没有剪辑任务/)).toHaveCount(0);
  await expect(main.getByText(/server_private_path/)).toHaveCount(0);
});

test("fixture first Mac pairing preserves the page draft and starts only on explicit submission", async ({
  page,
}) => {
  await setup(page);
  let paired = false;
  let ready = false;
  const creates: Record<string, unknown>[] = [];
  await page.route("**/api/editing/devices", (route) => {
    if (route.request().method() === "POST") {
      paired = true;
      return route.fulfill({
        json: {
          device: { ...device, online: false, ready: false, grants: [] },
          token: "fixture-once-token",
        },
      });
    }
    return route.fulfill({
      json: {
        items: paired
          ? [
              {
                ...device,
                online: ready,
                ready,
                grants: ready ? ["drama"] : [],
              },
            ]
          : [],
      },
    });
  });
  await page.route("**/api/editing/tasks", (route) => {
    creates.push(route.request().postDataJSON() as Record<string, unknown>);
    return route.fulfill({ json: { ...task, status: "waiting" } });
  });
  await page.route("**/api/editing/tasks/fixture-task", (route) =>
    route.fulfill({ json: task }),
  );
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/workspace/editing/new?title=雨夜");
  await page
    .getByRole("textbox", { name: "剪辑要求", exact: true })
    .fill("保留首次连接前的要求");
  await page.getByRole("button", { name: "生成连接凭证" }).click();
  await expect(page.getByLabel("一次性设备连接凭证")).toHaveValue(
    "fixture-once-token",
  );
  ready = true;
  await expect(page.getByText("Mac 已连接，剪辑环境已就绪")).toBeVisible({
    timeout: 10_000,
  });
  await expect(
    page.getByRole("textbox", { name: "剪辑要求", exact: true }),
  ).toHaveValue("保留首次连接前的要求");
  expect(creates).toHaveLength(0);
  await page
    .getByRole("combobox", { name: "已授权目录", exact: true })
    .selectOption("drama");
  await page.getByRole("button", { name: "开始剪辑", exact: true }).click();
  await expect(page).toHaveURL(/\/workspace\/editing\/fixture-task$/);
  expect(creates).toHaveLength(1);
  expect(creates[0]).toMatchObject({
    title: "雨夜",
    device_id: "mac",
    requirements: { instructions: "保留首次连接前的要求" },
    source_directory: { grant_id: "drama", relative_path: "." },
  });
  expect(errors).toEqual([]);
});
