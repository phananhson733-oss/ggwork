import { randomUUID } from "node:crypto";
import { readFileSync } from "node:fs";

import { expect, test, type Dialog } from "@playwright/test";

import type { SaveCommand, SavedPick } from "@/core/pick/api";

// Real HTTP + authentication + database + browser, with synthetic prior result/run.
// No API response is mocked: the single fault injects a lost response AFTER commit.
test("synthetic prior candidates save, recover receipt, reload, conflict, export and remove", async ({
  page,
  context,
}, info) => {
  const fixturePath = process.env.PICK_SELECTION_FIXTURE;
  test.skip(
    !fixturePath,
    "Requires a dedicated isolated synthetic_fixture account/result",
  );
  const fixture = JSON.parse(readFileSync(fixturePath!, "utf8")) as {
    origin: string;
    observed_execution: boolean;
    email: string;
    password: string;
    user_id: string;
    thread_id: string;
    result_id: string;
    item_ids: Record<string, string>;
  };
  expect(fixture.origin).toBe("synthetic_fixture");
  expect(fixture.observed_execution).toBe(false);
  const forbiddenRuns: string[] = [];
  await page.route(/\/runs(?:\/stream)?(?:\?|$)/, async (route) => {
    if (route.request().method() === "POST") {
      forbiddenRuns.push(route.request().url());
      await route.abort();
    } else await route.continue();
  });
  const login = await context.request.post("/api/v1/auth/login/local", {
    form: { username: fixture.email, password: fixture.password },
  });
  expect(login.ok()).toBe(true);
  const csrf = (await context.cookies()).find(
    (cookie) => cookie.name === "csrf_token",
  )!.value;
  const headers = { "X-CSRF-Token": csrf };
  // Dedicated account only. Restore repeats safely from its synthetic source.
  const initial = await context.request.get("/api/pick/selections");
  expect(initial.ok()).toBe(true);
  const prior = (await initial.json()) as { selections: SavedPick[] };
  for (const row of prior.selections) {
    expect(row.source_result_id).toBe(fixture.result_id);
    expect(
      (
        await context.request.patch(`/api/pick/selections/${row.id}`, {
          headers,
          data: {
            request_id: randomUUID(),
            expected_version: row.version,
            state: "removed",
          },
        })
      ).ok(),
    ).toBe(true);
  }
  await page.addInitScript(
    ({ owner, thread, result, items }) => {
      sessionStorage.setItem(
        `ggwork-pick:${JSON.stringify([owner, thread])}`,
        JSON.stringify({ result_id: result, item_ids: items, open: true }),
      );
    },
    {
      owner: fixture.user_id,
      thread: fixture.thread_id,
      result: fixture.result_id,
      items: Object.values(fixture.item_ids),
    },
  );
  let committed: SaveCommand | null = null;
  await page.route("**/api/pick/selections", async (route) => {
    if (route.request().method() !== "POST" || committed) {
      await route.continue();
      return;
    }
    committed = route.request().postDataJSON() as SaveCommand;
    const response = await route.fetch();
    expect(response.ok()).toBe(true);
    await route.abort("failed");
  });
  await page.goto(`/workspace/chats/${fixture.thread_id}`);
  await expect(page.getByRole("button", { name: "保存选中（3）" })).toBeEnabled(
    { timeout: 30000 },
  );
  await page.getByRole("button", { name: "保存选中（3）" }).click();
  await expect(page.getByRole("link", { name: "查看我的选剧" })).toBeVisible();
  expect(committed).not.toBeNull();
  const retry = await context.request.post("/api/pick/selections", {
    headers,
    data: committed,
  });
  expect(retry.ok()).toBe(true);
  const receipt = await context.request.get(
    `/api/pick/commands/${committed!.request_id}`,
  );
  expect(await retry.json()).toEqual(await receipt.json());
  await page.getByRole("link", { name: "查看我的选剧" }).click();
  await expect(
    page.getByRole("heading", { name: "我的选剧", exact: true }),
  ).toBeVisible();
  await expect(
    page
      .getByRole("navigation", { name: "选剧工作台" })
      .getByRole("link", { name: "我的选剧" }),
  ).toHaveAttribute("aria-current", "page");
  await page.reload();
  await expect(page.getByText("清单共 3 部")).toBeVisible();
  const row = page.locator("article").first();
  const note = row.getByLabel("个人备注");
  await note.fill("T6 local note");
  await row.getByRole("button", { name: "保存备注" }).click();
  await expect(row.getByText("备注已保存")).toBeVisible();
  await page.reload();
  await expect(note).toHaveValue("T6 local note");
  await note.fill("T6 history draft retained");
  let dialogs = 0;
  const unexpectedDialog = async (dialog: Dialog) => {
    dialogs++;
    await dialog.accept();
  };
  page.on("dialog", unexpectedDialog);
  await page
    .getByRole("navigation", { name: "选剧工作台" })
    .getByRole("link", { name: "我的选剧" })
    .click();
  page.off("dialog", unexpectedDialog);
  expect(dialogs).toBe(0);
  // Browser history traversal has no cancellable Next route hook; drafts remain in owner-scoped workspace memory.
  await page.goBack();
  await expect(page).toHaveURL(
    new RegExp(`/workspace/chats/${fixture.thread_id}$`),
  );
  await page.goForward();
  await expect(note).toHaveValue("T6 history draft retained");
  await expect(row.getByText("有未保存修改")).toBeVisible();
  await note.fill("T6 conflict draft retained");
  const list = (await (
    await context.request.get("/api/pick/selections")
  ).json()) as { selections: SavedPick[] };
  const current = list.selections[0]!;
  expect(
    (
      await context.request.patch(`/api/pick/selections/${current.id}`, {
        headers,
        data: {
          request_id: randomUUID(),
          expected_version: current.version,
          note: "T6 other tab",
        },
      })
    ).ok(),
  ).toBe(true);
  await row.getByRole("button", { name: "保存备注" }).click();
  await expect(row.getByText("服务器备注：T6 other tab")).toBeVisible();
  await expect(note).toHaveValue("T6 conflict draft retained");
  await row.getByRole("button", { name: "保留我的备注并采用新版本" }).click();
  await row.getByRole("button", { name: "保存备注" }).click();
  await expect(row.getByText("备注已保存")).toBeVisible();
  await row.getByRole("button", { name: "查看来源候选" }).click();
  await expect(row.getByRole("heading", { name: "本次候选" })).toBeVisible();
  const download = page.waitForEvent("download");
  await page.getByRole("button", { name: "导出参考清单" }).click();
  expect((await download).suggestedFilename()).toBe("我的选剧.csv");
  await expect(
    page.getByText("仅作选剧参考，未完成排期执行核对。"),
  ).toBeVisible();
  // Samples exercise the real responsive page; assertions do not claim screen-reader coverage.
  for (const width of [320, 768, 1280, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    if (width === 320 || width === 1440)
      await page.screenshot({
        path: info.outputPath(`selections-${width}.png`),
      });
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth,
      ),
    ).toBe(true);
  }
  await page.evaluate(() => document.documentElement.classList.add("dark"));
  await expect(note).toHaveCSS("color", "rgb(231, 237, 241)");
  await page.screenshot({
    path: info.outputPath("selections-dark-1440.png"),
    animations: "disabled",
  });
  await page.reload();
  for (let remaining = 3; remaining > 0; remaining--) {
    await page
      .locator('section[aria-label="个人选剧清单"] > article')
      .first()
      .getByRole("button", { name: "移出清单" })
      .click();
    if (remaining > 1)
      await expect(page.getByText(`清单共 ${remaining - 1} 部`)).toBeVisible();
  }
  await expect(page.getByText("还没有保存的剧目。")).toBeVisible();
  await page.reload();
  await expect(page.getByText("还没有保存的剧目。")).toBeVisible();
  expect(forbiddenRuns).toEqual([]);
});
