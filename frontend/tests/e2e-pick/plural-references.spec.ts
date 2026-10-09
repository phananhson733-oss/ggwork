import { expect, test } from "@playwright/test";

import {
  handleRunStream,
  mockLangGraphAPI,
  MOCK_THREAD_ID,
} from "../e2e/utils/mock-api";
import candidate from "../unit/core/pick/fixtures/backend-result.json" with { type: "json" };

test("explicit comparison sends both versions of the same identity and restores the frozen selection", async ({
  page,
}, info) => {
  const first = {
    ...candidate,
    id: "first",
    thread_id: MOCK_THREAD_ID,
    run_status: "success",
    items: candidate.items.slice(0, 1),
  };
  const second = {
    ...first,
    id: "second",
    catalog_batch_id: "older-batch",
    items: [
      {
        ...first.items[0]!,
        item_id: "second-item",
        reason: "Second snapshot evidence",
      },
    ],
  };
  const requests: Record<string, unknown>[] = [];
  mockLangGraphAPI(page, {
    threads: [
      { thread_id: MOCK_THREAD_ID, title: "Synthetic reference comparison" },
    ],
    runStreamHandler: async (route) => {
      requests.push(route.request().postDataJSON() as Record<string, unknown>);
      await handleRunStream(route, {}, undefined, {
        responseMessage: {
          type: "ai",
          id: "plural-answer",
          content: "合成浏览器回执",
          additional_kwargs: {
            pick_completion: {
              status: "incomplete",
              checker_version: "fixture",
              checked_at: "2026-10-08T00:00:00Z",
              correction_count: 0,
            },
          },
        },
      });
    },
  });
  await page.route("**/api/pick/**", async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname.endsWith("/notes"))
      return route.fulfill({ json: { item_facts: {} } });
    if (url.pathname === "/api/pick/results")
      return route.fulfill({ json: { results: [first, second] } });
    if (url.pathname.endsWith("/first")) return route.fulfill({ json: first });
    if (url.pathname.endsWith("/second"))
      return route.fulfill({ json: second });
    return route.fulfill({
      status: 503,
      json: { detail: "outside synthetic reference fixture" },
    });
  });
  await page.addInitScript(
    ({ threadId }) => {
      if (!sessionStorage.getItem("refs-fixture-initialized")) {
        sessionStorage.setItem(
          `ggwork-pick:${JSON.stringify(["default", threadId])}`,
          JSON.stringify({ result_id: "first", item_ids: [], open: true }),
        );
        sessionStorage.setItem("refs-fixture-initialized", "yes");
      }
    },
    { threadId: MOCK_THREAD_ID },
  );
  await page.goto(`/workspace/chats/${MOCK_THREAD_ID}`);
  await page.getByRole("button", { name: "比较两批候选", exact: true }).click();
  await page
    .getByRole("combobox", { name: "第一批候选", exact: true })
    .selectOption("first");
  await page
    .getByRole("combobox", { name: "第二批候选", exact: true })
    .selectOption("second");
  await expect(page.getByText("Second snapshot evidence")).toBeVisible();
  await expect(
    page.getByRole("button", { name: "引用所选两批到对话" }),
  ).toBeDisabled();
  await page
    .getByRole("checkbox", { name: "引用第一批：Feed Drama 1", exact: true })
    .check();
  await page
    .getByRole("checkbox", { name: "引用第二批：Feed Drama 1", exact: true })
    .check();
  await page.getByRole("button", { name: "引用所选两批到对话" }).click();
  await expect(page.getByLabel("本轮候选引用")).toContainText(
    "本轮引用 2 批候选",
  );
  await page.reload();
  await expect(page.getByLabel("本轮候选引用")).toContainText(
    "本轮引用 2 批候选",
  );
  for (const width of [375, 1280]) {
    await page.setViewportSize({ width, height: 900 });
    await expect(
      page.getByRole("button", { name: "取消多批引用" }),
    ).toBeVisible();
    await page.screenshot({
      path: info.outputPath(`plural-reference-${width}.png`),
      fullPage: true,
    });
  }
  await page.locator("textarea").last().fill("比较这两份快照的依据");
  await page.getByRole("button", { name: "Submit", exact: true }).click();
  await expect.poll(() => requests.length).toBe(1);
  const refs = {
    version: "pick-references-v1",
    references: [
      { result_id: "first", item_ids: [first.items[0]!.item_id] },
      { result_id: "second", item_ids: ["second-item"] },
    ],
  };
  expect(requests[0]!.context).toMatchObject({ pick_references: refs });
  expect(requests[0]!.context).not.toHaveProperty("pick_reference");
  const input = requests[0]!.input as {
    messages: Array<{ additional_kwargs: Record<string, unknown> }>;
  };
  expect(input.messages[0]!.additional_kwargs.pick_references).toEqual({
    ...refs,
    thread_id: MOCK_THREAD_ID,
  });
  await page.getByRole("button", { name: "取消多批引用" }).click();
  await expect(page.getByLabel("本轮候选引用")).toHaveCount(0);
});
