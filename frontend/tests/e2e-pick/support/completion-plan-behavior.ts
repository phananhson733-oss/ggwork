import { randomUUID } from "node:crypto";
import { writeFileSync } from "node:fs";

import {
  expect,
  type BrowserContext,
  type Page,
  type TestInfo,
} from "@playwright/test";

import {
  type Plan,
  type PlanRowInput,
  planRowInputSchema,
} from "@/core/pick/completion-types";

const finalTitle = "保留本地修改的长计划名称 Synthetic conflict draft";

/** Real owner cookies and Gateway writes; no fulfilled HTTP responses. */
export async function planConflictAndHistory(
  page: Page,
  context: BrowserContext,
  path: string,
  base: Plan,
  headers: Record<string, string>,
  info: TestInfo,
) {
  const receipts: Record<string, unknown>[] = [];
  const read = async (): Promise<Plan> => {
    const response = await context.request.get(path);
    expect(response.status()).toBe(200);
    return response.json() as Promise<Plan>;
  };
  const first = base.rows[0]!;
  const removed = base.rows[2]!;
  const addedId = randomUUID();
  const localNote = "Private local conflict note QA";
  await page.getByLabel("计划名称", { exact: true }).fill(finalTitle);
  await page.getByRole("button", { name: /编辑 / }).first().click();
  await page
    .getByRole("textbox", { name: "个人备注", exact: true })
    .fill(localNote);
  await page
    .getByRole("button", { name: "收起编辑（修改保留，尚未保存）" })
    .click();
  const rows = base.rows.map((row) =>
    planRowInputSchema.parse(
      Object.fromEntries(
        Object.keys(planRowInputSchema.shape).map((key) => [
          key,
          row[key as keyof typeof row],
        ]),
      ),
    ),
  );
  // Rebind the same synthetic selected source under a new row ID, while removing
  // an existing row. This exercises explicit presence and order decisions.
  const remoteRows = [
    { ...rows[2]!, row_id: addedId },
    rows[1]!,
    {
      ...rows[0]!,
      channel: "facebook",
      copy_text: "Remote copy QA",
      local_time: "2026-11-01T01:30",
      fold: 1,
    },
  ];
  const remoteResponse = await context.request.patch(path, {
    headers,
    data: {
      request_id: randomUUID(),
      expected_version: base.version,
      title: "Other tab",
      timezone: base.timezone,
      timezone_change: null,
      rows: remoteRows,
    },
  });
  expect(remoteResponse.status()).toBe(200);
  const remote = (await remoteResponse.json()) as Plan;
  expect(remote.version).toBe(base.version + 1);
  const stale = page.waitForResponse(
    (response) =>
      new URL(response.url()).pathname === path &&
      response.request().method() === "PATCH",
  );
  await page.getByRole("button", { name: "保存计划", exact: true }).click();
  expect((await stale).status()).toBe(409);
  const conflict = page.getByRole("region", { name: "计划冲突字段核对" });
  await expect(conflict).toBeVisible();
  const apply = conflict.getByRole("button", {
    name: "应用所选字段，以新版本继续",
  });
  await expect(apply).toBeDisabled();
  const choices: [string, "local" | "server"][] = [
    ["计划名称：保留版本", "local"],
    ["行顺序：保留版本", "local"],
    [`${first.row_id} · 目标渠道：保留版本`, "server"],
    [`${first.row_id} · 文案草稿：保留版本`, "server"],
    [`${first.row_id} · 当地时间：保留版本`, "server"],
    [`${first.row_id} · 重复时刻偏移：保留版本`, "server"],
    [`${first.row_id} · 个人备注：保留版本`, "local"],
    [`${removed.row_id} · 行保留状态：保留版本`, "local"],
    [`${addedId} · 行保留状态：保留版本`, "local"],
  ];
  await expect(conflict.getByRole("combobox")).toHaveCount(choices.length);
  for (const [index, [label, side]] of choices.entries()) {
    await conflict
      .getByRole("combobox", { name: label, exact: true })
      .selectOption(side);
    if (index < choices.length - 1) await expect(apply).toBeDisabled();
  }
  await conflict.getByRole("heading").scrollIntoViewIfNeeded();
  await page.screenshot({
    path: info.outputPath("real-multifield-conflict.png"),
    fullPage: true,
  });
  await apply.click();
  // Applying choices is an in-memory rebase; it must not silently persist.
  expect((await read()).version).toBe(remote.version);
  const saved = page.waitForResponse(
    (response) =>
      new URL(response.url()).pathname === path &&
      response.request().method() === "PATCH",
  );
  await page.getByRole("button", { name: "保存计划", exact: true }).click();
  const saveResponse = await saved;
  expect(saveResponse.status()).toBe(200);
  expect(saveResponse.request().postDataJSON().expected_version).toBe(
    remote.version,
  );
  const merged = await read();
  expect(merged.version).toBe(remote.version + 1);
  expect(merged.title).toBe(finalTitle);
  expect(merged.rows.map((row) => row.row_id)).toEqual(
    base.rows.map((row) => row.row_id),
  );
  expect(
    merged.rows.map((row) => [
      row.identity,
      row.source_result_id,
      row.source_item_id,
      row.selection_id,
    ]),
  ).toEqual(
    base.rows.map((row) => [
      row.identity,
      row.source_result_id,
      row.source_item_id,
      row.selection_id,
    ]),
  );
  expect(merged.rows[0]).toMatchObject({
    channel: "facebook",
    copy_text: "Remote copy QA",
    local_time: "2026-11-01T01:30",
    fold: 1,
    note: localNote,
  });
  expect(Date.parse(merged.rows[0]!.scheduled_at!)).toBe(
    Date.parse("2026-11-01T07:30:00Z"),
  );
  await page.reload();
  await page.getByRole("button", { name: /编辑 / }).first().click();
  await expect(
    page.getByRole("textbox", { name: "个人备注", exact: true }),
  ).toHaveValue(localNote);
  await expect(
    page.getByRole("textbox", { name: "发布文案", exact: true }),
  ).toHaveValue("Remote copy QA");
  await expect(page.getByLabel("重复时间的 UTC 偏移")).toHaveValue("1");
  receipts.push({
    kind: "field_conflict",
    before: base.version,
    remote: remote.version,
    merged: merged.version,
    choices,
    rows: merged.rows,
  });
  // Restore the original export channel/time through the UI for the subsequent
  // source-eligibility/CSV journey; the conflict receipt above keeps exact proof.
  await page.getByLabel("发布渠道").selectOption("youtube");
  await page.getByLabel(/当地发布时间/).fill(first.local_time!);
  await page
    .getByRole("button", { name: "收起编辑（修改保留，尚未保存）" })
    .click();
  await page.getByRole("button", { name: "保存计划", exact: true }).click();
  await expect(page.getByRole("status")).toContainText("已保存版本");

  for (const legacy of [false, true]) {
    const navigationPage = await context.newPage();
    try {
      if (legacy)
        await navigationPage.addInitScript(() =>
          Object.defineProperty(window, "navigation", {
            value: undefined,
            configurable: true,
          }),
        );
      const before = await read();
      await navigationPage.goto("/workspace/pick-plans");
      await navigationPage
        .getByRole("link", { name: finalTitle, exact: true })
        .click();
      const editor = navigationPage.getByLabel("计划名称", { exact: true });
      const draft = `Unsaved private history draft ${legacy}`;
      const committed = `Saved real history ${legacy}`;
      const assertPrivate = async () => {
        const state = await navigationPage.evaluate(() => ({
          history: JSON.stringify(history.state),
          local: JSON.stringify(localStorage),
          session: JSON.stringify(sessionStorage),
          next: history.state.__NA,
        }));
        expect(state.next).toBe(true);
        for (const value of [state.history, state.local, state.session]) {
          for (const privateText of [draft, committed, localNote])
            expect(value).not.toContain(privateText);
        }
        return state;
      };
      await editor.fill(draft);
      await navigationPage.evaluate(() => history.back());
      const dialog = navigationPage.getByRole("dialog", {
        name: "计划有未保存修改",
      });
      await expect(dialog).toBeVisible();
      await expect(navigationPage).toHaveURL(
        new RegExp(`/pick-plans/${base.id}$`),
      );
      await assertPrivate();
      await dialog.getByRole("button", { name: "留在本页" }).click();
      await expect(editor).toHaveValue(draft);
      expect((await read()).version).toBe(before.version);
      await navigationPage.evaluate(() => history.back());
      await dialog.getByRole("button", { name: "丢弃修改并离开" }).click();
      await expect(navigationPage).toHaveURL(/\/pick-plans$/);
      expect((await read()).title).toBe(finalTitle);
      await navigationPage.evaluate(() => history.forward());
      await expect(editor).toHaveValue(finalTitle);
      await navigationPage
        .getByRole("link", { name: "全部排期", exact: true })
        .click();
      await expect(navigationPage).toHaveURL(/\/pick-plans$/);
      await navigationPage.goBack();
      await editor.fill(committed);
      await navigationPage.evaluate(() => history.forward());
      await expect(dialog).toBeVisible();
      await expect(navigationPage).toHaveURL(
        new RegExp(`/pick-plans/${base.id}$`),
      );
      await assertPrivate();
      await navigationPage.screenshot({
        animations: "disabled",
        path: info.outputPath(
          `real-history-${legacy ? "fallback" : "native"}.png`,
        ),
      });
      await dialog.getByRole("button", { name: "保存并继续" }).click();
      await expect(navigationPage).toHaveURL(/\/pick-plans$/);
      const persisted = await read();
      expect(persisted.title).toBe(committed);
      expect(persisted.version).toBe(before.version + 1);
      expect(persisted.rows).toEqual(before.rows);
      await navigationPage.goBack();
      await expect(editor).toHaveValue(committed);
      receipts.push({
        kind: "history",
        mode: legacy ? "tracked fallback" : "native indices",
        before: before.version,
        after: persisted.version,
        privacy: await assertPrivate(),
      });
      await editor.fill(finalTitle);
      await navigationPage
        .getByRole("button", { name: "保存计划", exact: true })
        .click();
      await expect(navigationPage.getByRole("status")).toContainText(
        "已保存版本",
      );
    } finally {
      await navigationPage.close();
    }
  }
  await timeConflictEdges(context, rows[0]!, headers, receipts, info);
  await page.reload();
  await expect(page.getByLabel("计划名称", { exact: true })).toHaveValue(
    finalTitle,
  );
  writeFileSync(
    info.outputPath("real-plan-conflict-history.json"),
    JSON.stringify(receipts, null, 2),
  );
}

async function timeConflictEdges(
  context: BrowserContext,
  source: PlanRowInput,
  headers: Record<string, string>,
  receipts: Record<string, unknown>[],
  info: TestInfo,
) {
  for (const scenario of [
    "added-timezone",
    "normal-fold",
    "empty-fold",
  ] as const) {
    const page = await context.newPage();
    try {
      const row = {
        ...source,
        row_id: randomUUID(),
        local_time: scenario === "empty-fold" ? null : "2026-11-02T12:00",
        fold: null,
      };
      const created = await context.request.post("/api/pick/plans", {
        headers,
        data: {
          request_id: randomUUID(),
          title: `Synthetic edge ${scenario}`,
          timezone: "America/Chicago",
          rows: scenario === "added-timezone" ? [] : [row],
        },
      });
      expect(created.status()).toBe(200);
      const plan = (await created.json()) as Plan;
      const path = `/api/pick/plans/${plan.id}`;
      await page.goto(`/workspace/pick-plans/${plan.id}`);
      if (scenario === "added-timezone") {
        await page
          .locator("summary")
          .filter({ hasText: "更改计划时区" })
          .click();
        await page.getByLabel("新时区（IANA）").fill("Asia/Shanghai");
        await page.getByLabel("时间保留方式").selectOption("keep_local_time");
        await page.getByRole("button", { name: "预览时区变化" }).click();
        await page.getByRole("button", { name: "确认时区变化" }).click();
      } else {
        await page.getByRole("button", { name: /编辑 / }).click();
        await page
          .getByRole("textbox", { name: "个人备注", exact: true })
          .fill(`Private repair ${scenario}`);
        await page
          .getByRole("button", { name: "收起编辑（修改保留，尚未保存）" })
          .click();
      }
      const remote = await context.request.patch(path, {
        headers,
        data: {
          request_id: randomUUID(),
          expected_version: plan.version,
          title: plan.title,
          timezone: plan.timezone,
          timezone_change: null,
          rows: [{ ...row, local_time: "2026-11-01T01:30", fold: 1 }],
        },
      });
      expect(remote.status()).toBe(200);
      const server = (await remote.json()) as Plan;
      const conflictResponse = page.waitForResponse(
        (response) =>
          new URL(response.url()).pathname === path &&
          response.request().method() === "PATCH",
      );
      await page.getByRole("button", { name: "保存计划", exact: true }).click();
      expect((await conflictResponse).status()).toBe(409);
      const review = page.getByRole("region", { name: "计划冲突字段核对" });
      const choose = (label: string, side: string) =>
        review
          .getByRole("combobox", { name: label, exact: true })
          .selectOption(side);
      const apply = review.getByRole("button", {
        name: "应用所选字段，以新版本继续",
      });
      if (scenario === "added-timezone") {
        await choose("计划时区：保留版本", "local");
        await choose("行顺序：保留版本", "server");
        await choose(`${row.row_id} · 行保留状态：保留版本`, "server");
        await choose("时区冲突处理", "keep_instant");
        await expect(
          review.getByText(/转换后当地时间：2026-11-01T15:30/),
        ).toBeVisible();
      } else {
        await choose(`${row.row_id} · 当地时间：保留版本`, "local");
        await choose(`${row.row_id} · 重复时刻偏移：保留版本`, "server");
        await choose(`${row.row_id} · 个人备注：保留版本`, "local");
        await expect(apply).toBeDisabled();
        await expect(review.getByRole("alert")).toContainText(
          "所选偏移与所选当地时间不一致",
        );
        await review
          .getByRole("button", {
            name: `${row.row_id} · 清除不适用的偏移`,
            exact: true,
          })
          .click();
        await expect(
          review.getByText("已明确清除不适用的偏移；其他字段选择不变。"),
        ).toBeVisible();
      }
      await expect(apply).toBeEnabled();
      await (
        scenario === "added-timezone"
          ? review.getByText(/转换后当地时间：2026-11-01T15:30/)
          : review.getByText("已明确清除不适用的偏移；其他字段选择不变。")
      ).scrollIntoViewIfNeeded();
      await page.screenshot({
        animations: "disabled",
        path: info.outputPath(`real-time-edge-${scenario}.png`),
        fullPage: true,
      });
      await apply.click();
      const unchanged = (await (
        await context.request.get(path)
      ).json()) as Plan;
      expect(unchanged.version).toBe(server.version);
      const saved = page.waitForResponse(
        (response) =>
          new URL(response.url()).pathname === path &&
          response.request().method() === "PATCH",
      );
      await page.getByRole("button", { name: "保存计划", exact: true }).click();
      const response = await saved;
      expect(response.status()).toBe(200);
      const payload = response.request().postDataJSON();
      expect(payload.expected_version).toBe(server.version);
      const result = (await response.json()) as Plan;
      const expectedTime =
        scenario === "added-timezone" ? "2026-11-01T15:30" : row.local_time;
      expect(payload.rows[0]).toMatchObject({
        row_id: row.row_id,
        local_time: expectedTime,
        fold: null,
      });
      expect(result.rows[0]).toMatchObject({
        identity: source.identity,
        source_result_id: source.source_result_id,
        source_item_id: source.source_item_id,
        local_time: expectedTime,
        fold: null,
      });
      expect(result.version).toBe(server.version + 1);
      if (scenario === "added-timezone") {
        expect(result.timezone).toBe("Asia/Shanghai");
        expect(Date.parse(result.rows[0]!.scheduled_at!)).toBe(
          Date.parse("2026-11-01T07:30:00Z"),
        );
      } else if (scenario === "empty-fold")
        expect(result.rows[0]!.scheduled_at).toBeNull();
      else
        expect(Date.parse(result.rows[0]!.scheduled_at!)).toBe(
          Date.parse("2026-11-02T18:00:00Z"),
        );
      const persisted = await context.request.get(path);
      expect(await persisted.json()).toEqual(result);
      receipts.push({ kind: "time_edge", scenario, payload, result });
    } finally {
      await page.close();
    }
  }
}
