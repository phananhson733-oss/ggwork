import { randomUUID } from "node:crypto";

import { expect, test, type APIRequestContext } from "@playwright/test";

// The coordinator supplies the isolated instance's actual effective product budget.
function modelWaitMs() {
  const seconds = Number(process.env.PICK_E2E_RUN_TIMEOUT_SECONDS);
  if (!Number.isSafeInteger(seconds) || seconds <= 0) {
    throw new Error("Set PICK_E2E_RUN_TIMEOUT_SECONDS from the verified QA runtime");
  }
  return (seconds + 60) * 1000;
}

function validateTarget(url: string) {
  modelWaitMs();
  const target = new URL(url);
  expect(["localhost", "127.0.0.1", "[::1]"]).toContain(target.hostname);
}

async function validateRemoteQaOwner(request: APIRequestContext, url: string) {
  if (["localhost", "127.0.0.1"].includes(new URL(url).hostname)) return;
  const response = await request.get(`${url}/api/v1/auth/me`);
  expect(response.ok()).toBeTruthy();
  const user = (await response.json()) as {
    email: string;
    system_role: string;
  };
  expect(user.email).toBe(process.env.PICK_E2E_EMAIL);
  expect(user.email.startsWith("qa-azure-")).toBeTruthy();
  expect(user.system_role).toBe("user");
}

const createdThreads = new Set<string>();
test.beforeEach(async ({ page }) => {
  createdThreads.clear();
  page.on("request", (request) => {
    const match =
      /^\/api\/(?:langgraph\/)?threads\/([^/]+)\/runs\/stream$/.exec(
        new URL(request.url()).pathname,
      );
    if (request.method() === "POST" && match) createdThreads.add(match[1]!);
  });
});
test.afterEach(async ({ context }) => {
  const csrf = (await context.cookies()).find(
    (cookie) => cookie.name === "csrf_token",
  )?.value;
  if (!csrf) return;
  const url = process.env.PICK_E2E_URL ?? "http://localhost:3008";
  for (const threadId of createdThreads) {
    const response = await context.request.get(
      `${url}/api/threads/${threadId}/runs`,
    );
    if (!response.ok()) continue;
    const runs = (await response.json()) as {
      run_id: string;
      status: string;
    }[];
    for (const run of runs) {
      if (["pending", "running"].includes(run.status)) {
        await context.request.post(
          `${url}/api/threads/${threadId}/runs/${run.run_id}/cancel`,
          { headers: { "X-CSRF-Token": csrf } },
        );
      }
    }
  }
});

test("configured model preserves old evidence and retries a committed save", async ({
  page,
  context,
}) => {
  test.skip(
    !process.env.PICK_E2E_EMAIL || !process.env.PICK_E2E_PASSWORD,
    "Provide isolated QA account credentials",
  );
  const url = process.env.PICK_E2E_URL ?? "http://localhost:3008";
  validateTarget(url);
  const login = await context.request.post(`${url}/api/v1/auth/login/local`, {
    form: {
      username: process.env.PICK_E2E_EMAIL!,
      password: process.env.PICK_E2E_PASSWORD!,
    },
  });
  expect(login.ok()).toBeTruthy();
  await validateRemoteQaOwner(context.request, url);
  const csrf = (await context.cookies()).find(
    (c) => c.name === "csrf_token",
  )!.value;
  const marker = `Synthetic-${randomUUID().slice(0, 8)}`;
  await page.goto("/workspace/pick-data?tab=imports");
  await page.getByLabel("选择资料文件").setInputFiles({
    name: "synthetic.json",
    mimeType: "application/json",
    buffer: Buffer.from(
      JSON.stringify([
        {
          source: "synthetic-e2e",
          source_id: marker,
          title: marker,
          language: "en",
          availability: "active",
          signals: [
            {
              kind: "rank",
              source_ref: "fixture:original",
              observed_at: "2026-09-20",
              value: 7,
            },
          ],
        },
      ]),
    ),
  });
  await page.getByRole("button", { name: "导入资料" }).click();
  await expect(page.getByText("导入完成：1 条记录")).toBeVisible();
  await page.goto("/workspace/chats/new");
  await page.locator("textarea").fill("找1部英语剧，排除已选。");
  await page.getByRole("button", { name: "Submit", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "查看候选", exact: true }),
  ).toBeEnabled({ timeout: modelWaitMs() });
  await page.getByRole("button", { name: "查看候选", exact: true }).click();
  await expect(
    page.getByRole("checkbox", { name: `选择${marker}` }),
  ).toBeVisible();
  const threadId = new URL(page.url()).pathname.split("/").at(-1)!;
  const previous = (
    await (
      await context.request.get(`${url}/api/pick/results?thread_id=${threadId}`)
    ).json()
  ).results[0];
  const updateBatch = await context.request.post(`${url}/api/pick/imports`, {
    headers: { "X-CSRF-Token": csrf },
    multipart: {
      kind: "catalog",
      files: {
        name: "updated.json",
        mimeType: "application/json",
        buffer: Buffer.from(
          JSON.stringify([
            {
              source: "synthetic-e2e",
              source_id: marker,
              title: `Updated-${marker}`,
              language: "en",
              availability: "active",
              signals: [
                {
                  kind: "rank",
                  source_ref: "fixture:updated",
                  observed_at: "2026-09-21",
                  value: 99,
                },
              ],
            },
          ]),
        ),
      },
    },
  });
  expect(updateBatch.ok()).toBeTruthy();
  expect((await updateBatch.json()).id).not.toBe(previous.catalog_batch_id);
  const historical = await (
    await context.request.get(`${url}/api/pick/results/${previous.id}`)
  ).json();
  expect(historical.items).toEqual(previous.items);
  expect(historical.catalog_batch_id).toBe(previous.catalog_batch_id);
  await page.reload();
  await expect(
    page.getByRole("checkbox", { name: `选择${marker}` }),
  ).toBeVisible({ timeout: 30_000 });
  await page.locator("summary").filter({ hasText: "查看依据" }).click();
  await expect(page.getByText("rank · 7", { exact: true })).toBeVisible();
  await expect(page.getByText("rank · 99", { exact: true })).toHaveCount(0);
  await page.getByRole("checkbox", { name: `选择${marker}` }).check();
  await page.getByLabel("保存备注").fill("下周准备剪辑");
  await expect(page.getByRole("button", { name: "保存选中（1）" })).toBeEnabled(
    { timeout: modelWaitMs() },
  );
  const attempts: unknown[] = [];
  let originalReceipt: unknown;
  // Fault injection drops only the first response AFTER the real Gateway commits.
  // Queries, imports, command handling and persistence are never mocked.
  await page.route("**/api/pick/selections", async (route) => {
    if (route.request().method() !== "POST") return route.continue();
    attempts.push(route.request().postDataJSON());
    if (attempts.length === 1) {
      const committed = await route.fetch();
      expect(committed.ok()).toBeTruthy();
      originalReceipt = await committed.json();
      await route.abort("connectionreset");
    } else {
      await route.continue();
    }
  });
  await page.getByRole("button", { name: "保存选中（1）" }).click();
  await expect(page.getByRole("alert")).toBeVisible();
  const committedRows = (
    await (await context.request.get(`${url}/api/pick/selections`)).json()
  ).selections;
  expect(
    committedRows.filter(
      (row: { source_result_id: string }) =>
        row.source_result_id === previous.id,
    ),
  ).toHaveLength(1);
  await page.getByRole("button", { name: "保存选中（1）" }).click();
  await expect(page.getByText(/已保存 1 部/)).toBeVisible();
  expect(attempts).toHaveLength(2);
  expect(attempts[1]).toEqual(attempts[0]);
  const requestId = (attempts[0] as { request_id: string }).request_id;
  expect(
    await (
      await context.request.get(`${url}/api/pick/commands/${requestId}`)
    ).json(),
  ).toEqual(originalReceipt);
  await page.unroute("**/api/pick/selections");
  const selections = await context.request.get(`${url}/api/pick/selections`);
  const savedRows = (await selections.json()).selections;
  expect(
    savedRows.filter(
      (row: { source_result_id: string }) =>
        row.source_result_id === previous.id,
    ),
  ).toHaveLength(1);
  const saved = savedRows.find(
    (r: { snapshot_json: { title: string } }) =>
      r.snapshot_json.title === marker,
  );
  expect(saved.note).toBe("下周准备剪辑");
  const retryId = randomUUID();
  const command = {
    request_id: retryId,
    expected_version: saved.version,
    note: "已复核",
  };
  const update = await context.request.patch(
    `${url}/api/pick/selections/${saved.id}`,
    { headers: { "X-CSRF-Token": csrf }, data: command },
  );
  expect(update.ok()).toBeTruthy();
  await page.goto("/workspace/picks");
  await page.reload();
  await expect(page.getByText(marker, { exact: true })).toBeVisible();
  await expect(
    page.locator("article").filter({ hasText: marker }).getByLabel("个人备注"),
  ).toHaveValue("已复核");
});

test("refresh during an active run preserves the question and never invents a saved result", async ({
  page,
  context,
}) => {
  test.skip(
    !process.env.PICK_E2E_EMAIL || !process.env.PICK_E2E_PASSWORD,
    "Provide isolated QA account credentials",
  );
  const url = process.env.PICK_E2E_URL ?? "http://localhost:3008";
  validateTarget(url);
  const login = await context.request.post(`${url}/api/v1/auth/login/local`, {
    form: {
      username: process.env.PICK_E2E_EMAIL!,
      password: process.env.PICK_E2E_PASSWORD!,
    },
  });
  expect(login.ok()).toBeTruthy();
  await validateRemoteQaOwner(context.request, url);
  const before = (
    await (await context.request.get(`${url}/api/pick/selections`)).json()
  ).selections.length;
  const question = "请查询1部英语剧，包含已选，直接查询剧库并展示候选。";
  await page.goto("/workspace/chats/new");
  await page.locator("textarea").fill(question);
  const submitted = page.waitForRequest(
    (request) =>
      request.url().endsWith("/runs/stream") && request.method() === "POST",
  );
  await page.getByRole("button", { name: "Submit", exact: true }).click();
  expect((await submitted).postDataJSON().on_disconnect).toBe("continue");
  await page.waitForURL(/\/workspace\/chats\/(?!new)[^/]+$/);
  const threadId = new URL(page.url()).pathname.split("/").at(-1)!;
  await expect
    .poll(
      async () => {
        const rows = await (
          await context.request.get(`${url}/api/threads/${threadId}/runs`)
        ).json();
        return rows[0]?.status;
      },
      { timeout: 15_000 },
    )
    .toBe("running");
  await page.reload();
  await expect(
    page.getByRole("log").getByText(question, { exact: true }),
  ).toBeVisible({
    timeout: 30_000,
  });
  await expect
    .poll(
      async () => {
        const rows = await (
          await context.request.get(`${url}/api/threads/${threadId}/runs`)
        ).json();
        return rows[0]?.status;
      },
      { timeout: modelWaitMs() },
    )
    .toBe("success");
  const results = (
    await (
      await context.request.get(`${url}/api/pick/results?thread_id=${threadId}`)
    ).json()
  ).results;
  expect(results.length).toBeGreaterThan(0);
  await expect(
    page.getByRole("button", { name: "查看候选", exact: true }),
  ).toBeEnabled();
  expect(
    results.every(
      (result: { run_status: string }) => result.run_status === "success",
    ),
  ).toBeTruthy();
  expect(
    (await (await context.request.get(`${url}/api/pick/selections`)).json())
      .selections.length,
  ).toBe(before);
  await expect(page.locator("textarea")).toBeEnabled();
});

test("explicit stop cancels the run without changing personal selections", async ({
  page,
  context,
}) => {
  test.skip(
    !process.env.PICK_E2E_EMAIL || !process.env.PICK_E2E_PASSWORD,
    "Provide isolated QA account credentials",
  );
  const url = process.env.PICK_E2E_URL ?? "http://localhost:3008";
  validateTarget(url);
  const login = await context.request.post(`${url}/api/v1/auth/login/local`, {
    form: {
      username: process.env.PICK_E2E_EMAIL!,
      password: process.env.PICK_E2E_PASSWORD!,
    },
  });
  expect(login.ok()).toBeTruthy();
  await validateRemoteQaOwner(context.request, url);
  const before = (
    await (await context.request.get(`${url}/api/pick/selections`)).json()
  ).selections.length;
  await page.goto("/workspace/chats/new");
  await page
    .locator("textarea")
    .fill("请查询5部英语剧，包含已选，并读取候选详情说明依据。");
  await page.getByRole("button", { name: "Submit", exact: true }).click();
  await page.waitForURL(/\/workspace\/chats\/(?!new)[^/]+$/);
  const threadId = new URL(page.url()).pathname.split("/").at(-1)!;
  await expect(
    page
      .getByRole("button", { name: "Submit", exact: true })
      .locator("svg.lucide-square"),
  ).toBeVisible({ timeout: 30_000 });
  const cancellation = page.waitForResponse(
    (response) =>
      response.url().includes(`/threads/${threadId}/runs/`) &&
      response.url().includes("/cancel") &&
      response.request().method() === "POST",
  );
  await page.getByRole("button", { name: "Submit", exact: true }).click();
  expect((await cancellation).ok()).toBeTruthy();
  await expect
    .poll(
      async () =>
        (
          await (
            await context.request.get(`${url}/api/threads/${threadId}/runs`)
          ).json()
        )[0]?.status,
      { timeout: 30_000 },
    )
    .toBe("interrupted");
  await page.reload();
  await expect(
    page
      .getByRole("log")
      .getByText("请查询5部英语剧，包含已选，并读取候选详情说明依据。", {
        exact: true,
      }),
  ).toBeVisible();
  expect(
    (await (await context.request.get(`${url}/api/pick/selections`)).json())
      .selections.length,
  ).toBe(before);
  await expect(page.locator("textarea")).toBeEnabled();
});
