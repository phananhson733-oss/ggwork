import { readFileSync } from "node:fs";

import {
  expect,
  test,
  type APIRequestContext,
  type Page,
} from "@playwright/test";

/**
 * The pick data board rendered for real (P3; the gate before P3-6): a local
 * QA instance whose gateway and mirror share one PostgreSQL database built by
 * customizations/pick-workbench/tests/mirror/board_fixture.py, the frontend
 * from `pnpm build && pnpm start -p 3008` reading it through
 * PICK_MIRROR_READER_URL over TLS (docs/pick-workbench/local-run.md,
 * "选剧资料页 e2e"). It catches what the unit tests cannot: a version scope
 * that does not reach a nested component, and two requests sharing one.
 *
 * Needs PICK_E2E_EMAIL / PICK_E2E_PASSWORD (a QA account of that instance) and
 * PICK_BOARD_FIXTURE_JSON (the file `board_fixture.py up` printed). Without
 * them every test is skipped, and a skip is not a pass. Local targets only:
 * the fixture is synthetic and lives on this machine.
 */

const BASE_URL = process.env.PICK_E2E_URL ?? "http://localhost:3008";
const BOARD = "/workspace/pick-data";
const ERROR_TEXT = "选剧资料暂时打不开";
const SCOPE_ERROR = "outside a version scope";

type Versions = Readonly<{ dropped: number; v1: number; v2: number }>;

function fixtureVersions(): Versions | null {
  const file = process.env.PICK_BOARD_FIXTURE_JSON;
  if (!file) return null;
  const parsed = JSON.parse(readFileSync(file, "utf8")) as {
    versions?: { dropped?: unknown; v1?: unknown; v2?: unknown };
  };
  const { dropped, v1, v2 } = parsed.versions ?? {};
  if ([dropped, v1, v2].some((id) => typeof id !== "number"))
    throw new Error("PICK_BOARD_FIXTURE_JSON lacks versions.dropped/v1/v2");
  return { dropped, v1, v2 } as Versions;
}

function requireSetup(): Versions {
  const versions = fixtureVersions();
  test.skip(
    !process.env.PICK_E2E_EMAIL ||
      !process.env.PICK_E2E_PASSWORD ||
      versions === null,
    "Provide the QA account and PICK_BOARD_FIXTURE_JSON (see local-run.md)",
  );
  expect(["localhost", "127.0.0.1"]).toContain(new URL(BASE_URL).hostname);
  return versions!;
}

async function logIn(request: APIRequestContext) {
  const login = await request.post(`${BASE_URL}/api/v1/auth/login/local`, {
    form: {
      username: process.env.PICK_E2E_EMAIL!,
      password: process.env.PICK_E2E_PASSWORD!,
    },
  });
  expect(login.ok()).toBeTruthy();
}

async function expectBoard(page: Page, path: string, rows: readonly string[]) {
  const response = await page.goto(path);
  expect(response?.status()).toBe(200);
  const body = page.locator("body");
  for (const row of rows)
    await expect(page.getByText(row).first()).toBeVisible();
  await expect(body).not.toContainText(SCOPE_ERROR);
  await expect(body).not.toContainText(ERROR_TEXT);
}

// One page of each kind, with a row the fixture puts on it (plan:1644-1647).
const PAGES: readonly (readonly [string, readonly string[]])[] = [
  ["", ["c-1 的剧名（v2 改名）", "rs0001 的剧名"]],
  ["?tab=all", ["c-3 的剧名", "rs0003 的剧名"]],
  ["?tab=rank&rk=kd", ["c-4 的剧名", "c-1 的剧名（v2 改名）"]],
  ["?tab=rank&rk=rs_rr", ["rs0002 的剧名"]],
  ["?tab=rank&rk=rs_bill", ["rs0004 的剧名"]],
  ["?tab=rank&rk=rs_ledger", ["订单对账", "rs0004 的剧名"]],
  ["?tab=posted", ["title-1-文本"]],
  ["?tab=posted&sd=SD-1", ["title-1-文本", "← 返回发布记录"]],
  ["?tab=rules", ["ShortMax"]],
  ["?tab=row&row=c-2", ["c-2 的剧名"]],
  // rs0006 is a sibling book id: its page is the canonical rs0001's.
  ["?tab=row&row=reelshort-rs0006", ["rs0001 的剧名"]],
];

test.describe("pick data board on the fixture mirror", () => {
  test("every tab renders its fixture rows in the current version", async ({
    page,
    context,
  }) => {
    const versions = requireSetup();
    await logIn(context.request);
    for (const [query, rows] of PAGES) {
      await expectBoard(page, `${BOARD}${query}`, rows);
      await expect(page.getByTestId("board-header")).toContainText(
        `镜像 v${versions.v2} 采集于`,
      );
    }
    // The breadcrumb's current page is also a link named 选剧资料: the
    // sidebar's is the one this checks.
    await expect(
      page
        .getByRole("navigation", { name: "选剧工作台" })
        .getByRole("link", { name: "选剧资料", exact: true }),
    ).toHaveAttribute("aria-current", "page");
  });

  test("a pinned version shows its own rows and says it is pinned", async ({
    page,
    context,
  }) => {
    const versions = requireSetup();
    await logIn(context.request);
    await expectBoard(page, `${BOARD}?v=${versions.v1}`, ["c-1 的剧名"]);
    await expect(page.getByText("c-1 的剧名（v2 改名）")).toHaveCount(0);
    await expect(
      page.getByText(`正在看智能体当时用的版本 v${versions.v1}`),
    ).toBeVisible();
    await expect(
      page.getByRole("link", { name: "切换到最新" }),
    ).toHaveAttribute("href", BOARD);
  });

  test("ten concurrent requests alternating v each read their own version", async ({
    context,
  }) => {
    const versions = requireSetup();
    await logIn(context.request);
    const asked = Array.from({ length: 10 }, (_, n) =>
      n % 2 === 0 ? versions.v1 : versions.v2,
    );
    const pages = await Promise.all(
      asked.map(async (v) => {
        const response = await context.request.get(
          `${BASE_URL}${BOARD}?v=${v}`,
        );
        return { v, status: response.status(), html: await response.text() };
      }),
    );
    for (const { v, status, html } of pages) {
      expect(status).toBe(200);
      expect(html).toContain(`镜像 v${v} 采集于`);
      expect(html.includes("c-1 的剧名（v2 改名）")).toBe(v === versions.v2);
      expect(html).not.toContain(SCOPE_ERROR);
      expect(html).not.toContain(ERROR_TEXT);
    }
  });

  test("an unknown or pruned v falls back to the current version with a banner", async ({
    page,
    context,
  }) => {
    const versions = requireSetup();
    await logIn(context.request);
    await expectBoard(page, `${BOARD}?v=999999`, ["c-1 的剧名（v2 改名）"]);
    await expect(
      page.getByText(
        `链接里的版本 v999999 不存在或未发布，已显示当前版本 v${versions.v2}`,
      ),
    ).toBeVisible();
    await expectBoard(page, `${BOARD}?v=${versions.dropped}`, [
      "c-1 的剧名（v2 改名）",
    ]);
    await expect(
      page.getByText(
        `链接里的版本 v${versions.dropped} 已清理，已显示当前版本 v${versions.v2}`,
      ),
    ).toBeVisible();
  });

  test("the imports tab works without reading the mirror", async ({
    page,
    context,
  }) => {
    requireSetup();
    await logIn(context.request);
    await page.goto(`${BOARD}?tab=imports`);
    await expect(page.getByRole("heading", { level: 1 })).toHaveText(
      "选剧资料",
    );
    await expect(page.getByLabel("选择资料文件")).toBeVisible();
    await expect(page.getByTestId("pick-sync-mirror")).toContainText(
      `镜像 v${fixtureVersions()!.v2} 采集于`,
    );
  });

  test("a signed-out visitor is sent to login and sees no row", async ({
    browser,
  }) => {
    requireSetup();
    // The workspace layout redirects to /login without `next` (P3-5 keeps it).
    const fresh = await browser.newContext();
    const page = await fresh.newPage();
    await page.goto(`${BASE_URL}${BOARD}?tab=all`);
    await expect(page).toHaveURL(/\/login(\?|$)/);
    const rsc = await fresh.request.get(`${BASE_URL}${BOARD}?tab=all`, {
      headers: { RSC: "1" },
      maxRedirects: 0,
    });
    // No fixture title of any kind: catalog rows, ReelShort dramas, posts.
    expect(await rsc.text()).not.toMatch(
      /c-\d 的剧名|rs\d{4} 的剧名|title-\d-文本/,
    );
    await fresh.close();
  });
});
