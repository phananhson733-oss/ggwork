import { randomUUID } from "node:crypto";
import { readFileSync, writeFileSync } from "node:fs";

import { expect, test } from "@playwright/test";
import Papa from "papaparse";

import type { PlanRow } from "@/core/pick/completion-types";

import {
  actualBrowserZoom,
  visualMeasurements,
} from "./support/completion-visual";

// Actual HTTP and ordinary cookies throughout. This is a scripted model run,
// never a provider-model acceptance run and never a mocked Gateway response.
test("source Gateway: checked chat to saved selections and revision-bound execution CSV", async ({
  page,
  context,
}, info) => {
  const fixture = JSON.parse(
    readFileSync(process.env.PICK_COMPLETION_FIXTURE!, "utf8"),
  );
  expect(fixture.origin).toBe("synthetic_scripted");
  await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  const outside: string[] = [];
  await context.route("**/*", async (route) => {
    const url = new URL(route.request().url());
    if (!["127.0.0.1", "localhost"].includes(url.hostname)) {
      outside.push(url.origin);
      return route.abort();
    }
    return route.continue();
  });
  const login = await context.request.post("/api/v1/auth/login/local", {
    form: { username: fixture.email, password: fixture.password },
  });
  expect(login.ok()).toBe(true);
  const me = await (await context.request.get("/api/v1/auth/me")).json();
  expect(me.system_role).toBe("user");
  expect(me.id).toBe(fixture.owner);
  const csrf = (await context.cookies()).find(
    (cookie) => cookie.name === "csrf_token",
  )!.value;
  const headers = { "X-CSRF-Token": csrf };
  const titles = [
    "合成验收长中文标题：独立来源与明确可发的三部候选甲",
    "Synthetic English long title for scheduling evidence and historical identity B",
    "合成候选丙 Synthetic C",
  ];
  const source = titles.map((title, index) => ({
    source: "synthetic-browser",
    source_id: `browser-${index}`,
    title,
    language: "en",
    theater: "Synthetic",
    availability: "active",
    channel_rules: { youtube: "allowed" },
    listed_at: "2026-10-01",
  }));
  const imported = await context.request.post("/api/pick/imports", {
    headers,
    multipart: {
      kind: "catalog",
      source_ref: "synthetic-scripted-browser",
      files: {
        name: "synthetic.json",
        mimeType: "application/json",
        buffer: Buffer.from(JSON.stringify(source)),
      },
    },
  });
  expect(imported.status()).toBe(201);
  await page.goto("/workspace/chats/new");
  await page.locator("textarea").fill("查询三部合成候选并列出上架日期。");
  await page.getByRole("button", { name: "Submit", exact: true }).click();
  await expect(
    page.getByText("已核对：本次回答依据已确认。", { exact: true }),
  ).toBeVisible({ timeout: 90_000 });
  const threadId = new URL(page.url()).pathname.split("/").at(-1)!;
  const history = await (
    await context.request.get(`/api/threads/${threadId}/messages`)
  ).json();
  const final = history
    .map(
      (row: {
        content: {
          type: string;
          content: string;
          additional_kwargs: { pick_completion: { status: string } };
        };
      }) => row.content,
    )
    .filter((row: { type: string }) => row.type === "ai")
    .at(-1);
  expect(final.additional_kwargs.pick_completion.status).toBe("confirmed");
  await page
    .getByRole("button", { name: /Copy to clipboard|复制到剪贴板/ })
    .last()
    .click();
  expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(
    final.content,
  );
  await page.screenshot({
    path: info.outputPath("chat-checked.png"),
    fullPage: true,
  });
  await page.reload();
  await expect(
    page.getByText("已核对：本次回答依据已确认。", { exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "查看候选", exact: true }).click();
  for (const title of titles)
    await page
      .getByRole("checkbox", { name: `选择${title}`, exact: true })
      .check();
  await page
    .getByRole("button", { name: "保存选中（3）", exact: true })
    .click();
  await page.getByRole("link", { name: "查看我的选剧", exact: true }).click();
  await expect(page).toHaveURL(/\/workspace\/picks$/);
  await page.reload();
  await expect(page.getByText("清单共 3 部", { exact: true })).toBeVisible();
  for (const title of titles)
    await page
      .getByRole("checkbox", { name: `加入排期：${title}`, exact: true })
      .check();
  await page.getByRole("link", { name: "用所选剧目创建排期草稿" }).click();
  await page
    .getByLabel("新计划名称")
    .fill("合成真实链路排期 Synthetic execution plan");
  await page.getByRole("button", { name: "确认选择并创建草稿" }).click();
  await expect(page).toHaveURL(/\/workspace\/pick-plans\/[a-f0-9]{32}$/);
  const planPath = new URL(page.url()).pathname.replace(
    "/workspace/pick-plans",
    "/api/pick/plans",
  );
  await page.getByRole("button", { name: "核对执行条件" }).click();
  await expect(
    page.getByRole("button", { name: /确认版本.*生成执行表/ }),
  ).toHaveCount(0);
  await page.screenshot({
    path: info.outputPath("plan-blocked.png"),
    fullPage: true,
  });
  for (let index = 0; index < 3; index++) {
    await page.getByRole("button", { name: /编辑 / }).nth(index).click();
    await page
      .getByLabel("发布账号", { exact: true })
      .fill(`synthetic-account-${index}`);
    await page.getByLabel("发布渠道").selectOption("youtube");
    await page.getByLabel(/当地发布时间/).fill(`2026-11-02T1${index}:30`);
    await page
      .getByLabel("发布文案", { exact: true })
      .fill('Synthetic, "quoted" copy\nSecond line');
    await page
      .getByRole("button", { name: "收起编辑（修改保留，尚未保存）" })
      .click();
  }
  await page.getByRole("button", { name: "保存计划", exact: true }).click();
  await expect(page.getByRole("status")).toContainText("已保存版本");
  await page.reload();
  const original = await (await context.request.get(planPath)).json();
  expect(original.rows).toHaveLength(3);
  await page.locator("summary").filter({ hasText: "更改计划时区" }).click();
  await page.getByLabel("新时区（IANA）").fill("America/Chicago");
  await page.getByLabel("时间保留方式").selectOption("keep_instant");
  await page.getByRole("button", { name: "预览时区变化" }).click();
  await page.getByRole("button", { name: "确认时区变化" }).click();
  await page.getByRole("button", { name: "保存计划", exact: true }).click();
  await expect(page.getByRole("status")).toContainText("已保存版本");
  const zoned = await (await context.request.get(planPath)).json();
  expect(zoned.timezone).toBe("America/Chicago");
  expect(zoned.rows.map((r: PlanRow) => r.scheduled_at)).toEqual(
    original.rows.map((r: PlanRow) => r.scheduled_at),
  );
  // The competing tab uses the same real owner/version contract.
  await page
    .getByLabel("计划名称", { exact: true })
    .fill("保留本地修改的长计划名称 Synthetic conflict draft");
  const changed = await context.request.patch(planPath, {
    headers,
    data: {
      request_id: randomUUID(),
      expected_version: zoned.version,
      title: "Other tab",
      timezone: zoned.timezone,
      timezone_change: null,
      rows: zoned.rows.map((r: PlanRow) => ({
        row_id: r.row_id,
        identity: r.identity,
        source_result_id: r.source_result_id,
        source_item_id: r.source_item_id,
        selection_id: r.selection_id,
        account: r.account,
        channel: r.channel,
        local_time: r.local_time,
        fold: r.fold,
        copy_text: r.copy_text,
        note: r.note,
      })),
    },
  });
  expect(changed.ok()).toBe(true);
  await page.getByRole("button", { name: "保存计划", exact: true }).click();
  await expect(
    page.getByText("本地输入仍保留。比较后决定采用哪一份。"),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "保留本地修改，以新版本继续" })
    .click();
  await page.getByRole("button", { name: "保存计划", exact: true }).click();
  await expect(page.getByRole("status")).toContainText("已保存版本");
  await page.getByRole("button", { name: "核对执行条件" }).click();
  // A current source change makes exactly one of the three complete rows unknown.
  const unknown = structuredClone(source);
  unknown[2]!.channel_rules.youtube = "unknown";
  const replaceSource = async (records: typeof source) => {
    const response = await context.request.post("/api/pick/imports", {
      headers,
      multipart: {
        kind: "catalog",
        source_ref: "synthetic-current-rule-refresh",
        files: {
          name: "synthetic-current.json",
          mimeType: "application/json",
          buffer: Buffer.from(JSON.stringify(records)),
        },
      },
    });
    expect(response.status()).toBe(201);
  };
  await replaceSource(unknown);
  const blockedResponse = page.waitForResponse(
    (response) =>
      response.url().endsWith("/preview") &&
      response.request().method() === "POST",
  );
  await page.getByRole("button", { name: "核对执行条件" }).click();
  const blockedPreview = await (await blockedResponse).json();
  expect(blockedPreview.checks).toHaveLength(3);
  expect(
    blockedPreview.checks.filter(
      (check: { status: string }) => check.status === "blocked",
    ),
  ).toHaveLength(1);
  expect(blockedPreview.exportable).toBe(false);
  await expect(
    page.getByRole("button", { name: /确认版本.*生成执行表/ }),
  ).toHaveCount(0);
  await replaceSource(source);
  const readyResponse = page.waitForResponse(
    (response) =>
      response.url().endsWith("/preview") &&
      response.request().method() === "POST",
  );
  await page.getByRole("button", { name: "核对执行条件" }).click();
  const readyPreview = await (await readyResponse).json();
  expect(readyPreview.exportable).toBe(true);
  const exportResponse = page.waitForResponse(
    (response) =>
      response.url().endsWith("/exports") &&
      response.request().method() === "POST",
  );
  await page.getByRole("button", { name: /确认版本.*生成执行表/ }).click();
  const receipt = await (await exportResponse).json();
  expect(receipt.plan_version).toBe(readyPreview.plan.version);
  expect(receipt.preview_id).toBe(readyPreview.preview_id);
  const downloadEvent = page.waitForEvent("download");
  await page.getByRole("button", { name: "下载同一执行表" }).click();
  const download = await downloadEvent;
  const exportPath = info.outputPath("execution.csv");
  await download.saveAs(exportPath);
  const bytes = readFileSync(exportPath);
  expect(bytes.subarray(0, 3)).toEqual(Buffer.from([0xef, 0xbb, 0xbf]));
  expect(bytes.toString("utf8")).toContain("America/Chicago");
  expect(bytes.toString("utf8")).toContain("synthetic-account-0");
  const csv = Papa.parse<Record<string, string>>(bytes.toString("utf8"), {
    header: true,
    skipEmptyLines: true,
  });
  expect(csv.errors).toEqual([]);
  expect(csv.data).toHaveLength(3);
  for (const row of csv.data) {
    expect(row.plan_id).toBe(zoned.id);
    expect(row.plan_version).toBe(String(receipt.plan_version));
    expect(row.timezone).toBe("America/Chicago");
  }
  const measurements = [];
  for (const theme of ["light", "dark"]) {
    await page.evaluate((value) => {
      localStorage.setItem("theme", value);
      document.documentElement.classList.toggle("dark", value === "dark");
    }, theme);
    await page.evaluate(
      () =>
        new Promise<void>((resolve) =>
          requestAnimationFrame(() => requestAnimationFrame(() => resolve())),
        ),
    );
    await expect
      .poll(() =>
        page.evaluate(
          () =>
            document
              .getAnimations()
              .filter(
                (animation) =>
                  animation instanceof CSSTransition &&
                  animation.playState === "running",
              ).length,
        ),
      )
      .toBe(0);
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
        path: info.outputPath(`plan-${theme}-${width}.png`),
        fullPage: true,
        animations: "disabled",
      });
      measurements.push({
        theme,
        width,
        measurements: await visualMeasurements(page),
      });
    }
  }
  await page.setViewportSize({ width: 320, height: 900 });
  const editButton = page.getByRole("button", { name: /编辑 / }).first();
  await editButton.focus();
  await page.keyboard.press("Enter");
  await expect(page.getByLabel("发布账号", { exact: true })).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(page.getByLabel("发布渠道")).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(editButton).toBeFocused();
  await page.emulateMedia({ reducedMotion: "reduce", forcedColors: "active" });
  await page.screenshot({
    path: info.outputPath("plan-high-contrast-reduced-motion.png"),
    fullPage: true,
  });
  writeFileSync(
    info.outputPath("plan-accessibility-tree.yml"),
    await page.locator("body").ariaSnapshot(),
  );
  writeFileSync(
    info.outputPath("visual-measurements.json"),
    JSON.stringify(measurements, null, 2),
  );
  const zoom = await actualBrowserZoom(context, page.url(), info);
  writeFileSync(
    info.outputPath("native-zoom.json"),
    JSON.stringify(zoom, null, 2),
  );
  expect(outside).toEqual([]);
  writeFileSync(
    info.outputPath("source-evidence.json"),
    JSON.stringify(
      {
        mode: "source",
        ordinary_user: true,
        real_gateway: true,
        postgres: true,
        provider_calls: 0,
        thread_id: threadId,
        plan_id: zoned.id,
        checked_final: final.additional_kwargs.pick_completion,
        screenshots: "actual rendered browser",
        screen_reader: "not exercised",
      },
      null,
      2,
    ),
  );
});
