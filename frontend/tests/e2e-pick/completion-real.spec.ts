import { readFileSync, writeFileSync } from "node:fs";

import { expect, test, type Dialog } from "@playwright/test";
import Papa from "papaparse";

import type { PlanRow } from "@/core/pick/completion-types";

import { planConflictAndHistory } from "./support/completion-plan-behavior";
import {
  actualBrowserZoom,
  visualMeasurements,
  voiceoverSession,
} from "./support/completion-visual";

// Actual HTTP and ordinary cookies throughout. This is a scripted model run,
// never a provider-model acceptance run and never a mocked Gateway response.
test("real Gateway: checked chat to saved selections and revision-bound execution CSV", async ({
  page,
  context,
}, info) => {
  const fixture = JSON.parse(
    readFileSync(process.env.PICK_COMPLETION_FIXTURE!, "utf8"),
  );
  expect(fixture.origin).toBe("synthetic_scripted");
  const screenMeasurements: {
    screen: string;
    theme: string;
    width: number;
    scope: string;
    measurement: Awaited<ReturnType<typeof visualMeasurements>>;
  }[] = [];
  const detailMeasurements: Record<string, unknown>[] = [];
  const captureScreen = async (screen: string) => {
    const dataScreen = screen.startsWith("data");
    for (const theme of ["light", "dark"]) {
      await page.evaluate((value) => {
        localStorage.setItem("theme", value);
        document.documentElement.classList.toggle("dark", value === "dark");
      }, theme);
      for (const width of [320, 768, 1280, 1440]) {
        await page.setViewportSize({ width, height: 900 });
        if (dataScreen) {
          const targets = [
            ["KalosTV", page.getByRole("link", { name: /^KalosTV\s*0$/ })],
            ["StarShort", page.getByRole("link", { name: /^StarShort\s*2$/ })],
            [
              "collapse",
              page.getByRole("link", { name: "收起其他剧场", exact: true }),
            ],
            [
              "YouTube toggle",
              page.getByRole("link", { name: /^YouTube\s*可发$/ }),
            ],
            [
              "search input",
              page.getByLabel("搜索剧名、中文名、行键或 book_id", {
                exact: true,
              }),
            ],
            [
              "search button",
              page.getByRole("button", { name: "搜索", exact: true }),
            ],
          ] as const;
          for (const [label, target] of targets) {
            await target.scrollIntoViewIfNeeded();
            await expect(target).toBeVisible();
            await expect(target).toBeEnabled();
            await expect(target).not.toHaveAttribute("aria-disabled", "true");
            await target.focus();
            await page.keyboard.press("Tab");
            await page.keyboard.press("Shift+Tab");
            await expect(target).toBeFocused();
            const bounds = await target.boundingBox();
            expect(bounds!.width).toBeGreaterThanOrEqual(44);
            expect(bounds!.height).toBeGreaterThanOrEqual(44);
            const facts = await target.evaluate((element) => ({
              tag: element.tagName,
              text: element.textContent,
              opacity: getComputedStyle(element).opacity,
              decorative: !!element.closest('[aria-hidden="true"]'),
              countOpacity: element.querySelector("small")
                ? getComputedStyle(element.querySelector("small")!).opacity
                : null,
            }));
            expect(facts.decorative).toBe(false);
            expect(facts.opacity).toBe("1");
            if (facts.countOpacity !== null)
              expect(facts.countOpacity).toBe("1");
            if (label === "KalosTV")
              await page.screenshot({
                path: info.outputPath(`data-chips-${theme}-${width}.png`),
                animations: "disabled",
              });
            const colors = await visualMeasurements(page, "main");
            expect(colors.focus!.outlineStyle).toBe("solid");
            expect(colors.focus!.outlineWidth).toBe("2px");
            expect(colors.focus!.outlineContrast).toBeGreaterThanOrEqual(3);
            detailMeasurements.push({
              screen,
              theme,
              width,
              label,
              bounds,
              facts,
              focus: colors.focus,
            });
          }
          const provenance = page
            .locator("td > div")
            .filter({ hasText: "source_table-6-文本" })
            .last();
          await provenance.scrollIntoViewIfNeeded();
          await expect(provenance).toBeVisible();
          const facts = await provenance.evaluate((element) => ({
            text: element.textContent,
            opacity: getComputedStyle(element).opacity,
            decorative: !!element.closest('[aria-hidden="true"]'),
          }));
          expect(facts.decorative).toBe(false);
          expect(facts.opacity).toBe("1");
          await page.screenshot({
            path: info.outputPath(`data-provenance-${theme}-${width}.png`),
            animations: "disabled",
          });
          const provenanceColors = (
            await visualMeasurements(page, "main")
          ).text.filter((sample) =>
            sample.sample.includes("source_table-6-文本"),
          );
          expect(provenanceColors.length).toBeGreaterThan(0);
          for (const sample of provenanceColors)
            expect(sample.ratio).toBeGreaterThanOrEqual(4.5);
          detailMeasurements.push({
            screen,
            theme,
            width,
            label: "provenance",
            facts,
            colors: provenanceColors,
          });
        }
        if (screen === "chat-checked") {
          const reply = page
            .locator(".is-assistant")
            .filter({ has: page.getByLabel("最终回答核对状态") })
            .last();
          const readRanges = () =>
            reply.evaluate((element) => {
              const replyBox = element.getBoundingClientRect();
              const mainBox = element.closest("main")!.getBoundingClientRect();
              const left = Math.max(0, replyBox.left, mainBox.left);
              const right = Math.min(innerWidth, replyBox.right, mainBox.right);
              const walker = document.createTreeWalker(
                element,
                NodeFilter.SHOW_TEXT,
              );
              const tokens: {
                token: string;
                rects: { left: number; right: number }[];
              }[] = [];
              let node: Node | null;
              while ((node = walker.nextNode())) {
                for (const match of (node.textContent ?? "").matchAll(
                  /\[result:[^\]]+\]/g,
                )) {
                  const range = document.createRange();
                  range.setStart(node, match.index);
                  range.setEnd(node, match.index + match[0].length);
                  tokens.push({
                    token: match[0],
                    rects: [...range.getClientRects()].map((rect) => ({
                      left: rect.left,
                      right: rect.right,
                    })),
                  });
                }
              }
              return { left, right, tokens };
            });
          await expect
            .poll(async () => {
              const measured = await readRanges();
              return (
                measured.tokens.length > 0 &&
                measured.tokens.every((token) => token.rects.length > 0)
              );
            })
            .toBe(true);
          const ranges = await readRanges();
          expect(ranges.tokens.length).toBeGreaterThan(0);
          for (const token of ranges.tokens) {
            expect(token.rects.length).toBeGreaterThan(0);
            for (const rect of token.rects) {
              expect(rect.left).toBeGreaterThanOrEqual(ranges.left - 1);
              expect(rect.right).toBeLessThanOrEqual(ranges.right + 1);
            }
          }
          detailMeasurements.push({
            screen,
            theme,
            width,
            label: "literal citation ranges",
            ranges,
          });
        }
        if (dataScreen || screen === "chat-checked") {
          const action = dataScreen
            ? page
                .getByRole("navigation", { name: "选剧资料分页" })
                .getByRole("link")
                .first()
            : page
                .getByRole("button", {
                  name: /(?:Copy|Copied) to clipboard|复制到剪贴板|已复制/,
                })
                .last();
          await action.focus();
          await page.keyboard.press("Tab");
          await page.keyboard.press("Shift+Tab");
          await expect(action).toBeFocused();
          await expect
            .poll(() =>
              action.evaluate((element) => {
                const box = element.getBoundingClientRect();
                const hit = document.elementFromPoint(
                  box.x + box.width / 2,
                  box.y + box.height / 2,
                );
                let current: Element | null = element;
                while (current) {
                  const style = getComputedStyle(current);
                  if (
                    Number(style.opacity) < 0.99 ||
                    style.visibility !== "visible"
                  )
                    return false;
                  current = current.parentElement;
                }
                return hit === element || (!!hit && element.contains(hit));
              }),
            )
            .toBe(true);
          const bounds = await action.boundingBox();
          expect(bounds!.width).toBeGreaterThanOrEqual(44);
          expect(bounds!.height).toBeGreaterThanOrEqual(44);
        }
        await page.screenshot({
          path: info.outputPath(`${screen}-${theme}-${width}.png`),
          fullPage: true,
          animations: "disabled",
        });
        const measured = await visualMeasurements(page, "main");
        if (dataScreen) {
          await expect(page.locator("main tbody").first()).toBeVisible();
          const cells = (
            await visualMeasurements(page, "main tbody")
          ).text.filter(
            (sample) => !["·", "/", "›", "↗"].includes(sample.sample),
          );
          expect(cells.length).toBeGreaterThan(0);
          expect(cells.filter((sample) => sample.ratio < 4.5)).toEqual([]);
          const pageSize = page.getByText("每页", { exact: true });
          await pageSize.scrollIntoViewIfNeeded();
          await expect(pageSize).toBeVisible();
          const pagerSummary = page.getByText(
            /^第\s*\d[\s\S]*页\s*·\s*本页[\s\S]*行[\s\S]*共[\s\S]*条$/,
          );
          await expect(pagerSummary).toHaveCount(1);
          await expect(pagerSummary).toBeVisible();
          const summaryText = (await pagerSummary.textContent())!
            .replace(/\s+/g, " ")
            .trim();
          const allPagerText = (await visualMeasurements(page, "main")).text;
          const summarySample = allPagerText.find(
            (sample) =>
              sample.sample.replace(/\s+/g, " ").trim() === summaryText,
          );
          const sizeSample = allPagerText.find(
            (sample) => sample.sample === "每页",
          );
          expect(summarySample).toBeDefined();
          expect(sizeSample).toBeDefined();
          const pagerText = [summarySample!, sizeSample!];
          for (const sample of pagerText)
            expect(sample.ratio).toBeGreaterThanOrEqual(4.5);
          if (screen === "data-with-off") {
            const delisted = page
              .locator("main tbody tr")
              .filter({ has: page.getByText(/^下架\s*\d{4}/) })
              .first();
            await delisted.scrollIntoViewIfNeeded();
            await expect(delisted).toBeVisible();
            await expect(delisted).toHaveCSS("opacity", "1");
            await page.screenshot({
              path: info.outputPath(`delisted-${theme}-${width}.png`),
              animations: "disabled",
            });
          }
          detailMeasurements.push({
            screen,
            theme,
            width,
            label: "meaningful cells and pagination",
            cells,
            pagerText,
          });
        }
        if (dataScreen || screen === "chat-checked") {
          expect(measured.focus?.outlineStyle).toBe("solid");
          expect(measured.focus!.outlineContrast).toBeGreaterThanOrEqual(3);
        }
        screenMeasurements.push({
          screen,
          theme,
          width,
          scope: "business main element; inherited controls included",
          measurement: measured,
        });
      }
    }
    await page.setViewportSize({ width: 1280, height: 900 });
    await page.evaluate(() => {
      localStorage.setItem("theme", "light");
      document.documentElement.classList.remove("dark");
    });
  };
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
  if (fixture.tls) {
    await page.goto("/workspace/pick-data");
    await expect(
      page.getByRole("heading", { name: "选剧资料", exact: true }),
    ).toBeVisible();
    await expect(page.getByTestId("board-header")).toContainText("镜像 v");
    await captureScreen("data");
    await page.goto("/workspace/pick-data?tab=all&off=1");
    await expect(page.getByText(/^下架\s*\d{4}/).first()).toBeVisible();
    await captureScreen("data-with-off");
  }
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
  await captureScreen("chat-checked");
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
  await captureScreen("selections");
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
  await planConflictAndHistory(page, context, planPath, zoned, headers, info);
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
  const measurements: {
    theme: string;
    width: number;
    measurements: Awaited<ReturnType<typeof visualMeasurements>>;
  }[] = [];
  const focusMeasurements: {
    theme: string;
    label: string;
    measurement: Awaited<ReturnType<typeof visualMeasurements>>["focus"];
  }[] = [];
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
    await page.setViewportSize({ width: 320, height: 900 });
    const firstEdit = page.getByRole("button", { name: /编辑 / }).first();
    await firstEdit.focus();
    await page.keyboard.press("Enter");
    await expect(page.getByLabel("发布账号", { exact: true })).toBeFocused();
    for (const label of [
      "发布账号",
      "发布渠道",
      "当地发布时间",
      "发布文案",
      "个人备注",
    ]) {
      await page.getByLabel(label).focus();
      await page.screenshot({
        path: info.outputPath(`focus-${theme}-${label}.png`),
        animations: "disabled",
      });
      focusMeasurements.push({
        theme,
        label,
        measurement: (await visualMeasurements(page)).focus,
      });
    }
    await page.keyboard.press("Escape");
    await expect(firstEdit).toBeFocused();
    await page.screenshot({
      path: info.outputPath(`focus-${theme}-return-button.png`),
      animations: "disabled",
    });
    focusMeasurements.push({
      theme,
      label: "row edit button",
      measurement: (await visualMeasurements(page)).focus,
    });
    await page.getByLabel("计划名称", { exact: true }).focus();
    await page.screenshot({
      path: info.outputPath(`focus-${theme}-plan-title.png`),
      animations: "disabled",
    });
    focusMeasurements.push({
      theme,
      label: "plan title",
      measurement: (await visualMeasurements(page)).focus,
    });
    await firstEdit.focus();
    await page.keyboard.press("Enter");
    const localTime = page.getByLabel(/当地发布时间/);
    await localTime.fill("2026-03-08T02:30");
    await expect(localTime).toHaveAttribute("aria-invalid", "true");
    await expect(
      page.getByRole("button", {
        name: "保存计划",
        exact: true,
        includeHidden: true,
      }),
    ).toBeDisabled();
    await localTime.focus();
    await page.screenshot({
      path: info.outputPath(`focus-${theme}-invalid-dst.png`),
      animations: "disabled",
    });
    focusMeasurements.push({
      theme,
      label: "invalid DST native datetime",
      measurement: (await visualMeasurements(page)).focus,
    });
    await page.keyboard.press("Escape");
    const discard = async (dialog: Dialog) => {
      expect(dialog.type()).toBe("beforeunload");
      await dialog.accept();
    };
    page.on("dialog", discard);
    await page.reload();
    page.off("dialog", discard);
    await expect(page.getByLabel("计划名称", { exact: true })).toHaveValue(
      "保留本地修改的长计划名称 Synthetic conflict draft",
    );
  }
  writeFileSync(
    info.outputPath("focus-measurements.json"),
    JSON.stringify(focusMeasurements, null, 2),
  );
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
  // Real saved draft read failure: no substituted JSON or fake success response.
  const failedRead = await context.newPage();
  let blockRead = true;
  await failedRead.route(`**${planPath}`, (route) =>
    blockRead && route.request().method() === "GET"
      ? route.abort("failed")
      : route.continue(),
  );
  await failedRead.goto(page.url());
  await expect(
    failedRead.getByText("排期读取失败或无权访问。", { exact: true }),
  ).toBeVisible({ timeout: 20_000 });
  await expect(failedRead.getByText(/共 0 行/)).toHaveCount(0);
  blockRead = false;
  await failedRead
    .getByRole("button", { name: "重新读取", exact: true })
    .click();
  await expect(failedRead.getByLabel("计划名称", { exact: true })).toHaveValue(
    "保留本地修改的长计划名称 Synthetic conflict draft",
  );
  await failedRead.close();
  if (process.env.PICK_COMPLETION_VOICEOVER === "1")
    await voiceoverSession(context, page.url(), info);
  writeFileSync(
    info.outputPath("screen-measurements.json"),
    JSON.stringify(screenMeasurements, null, 2),
  );
  writeFileSync(
    info.outputPath("business-detail-measurements.json"),
    JSON.stringify(detailMeasurements, null, 2),
  );
  expect(outside).toEqual([]);
  writeFileSync(
    info.outputPath("journey-evidence.json"),
    JSON.stringify(
      {
        mode: fixture.mode,
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
