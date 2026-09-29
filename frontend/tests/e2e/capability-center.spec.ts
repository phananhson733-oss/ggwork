import { mkdir } from "node:fs/promises";
import path from "node:path";

import { expect, test, type Page } from "@playwright/test";

import { mockLangGraphAPI } from "./utils/mock-api";

const skills = [
  {
    name: "deep-research",
    description:
      "Search, cross-check sources, and write a detailed research report.",
  },
  {
    name: "data-analysis",
    description: "Analyze structured data and create charts.",
  },
  {
    name: "academic-paper-review",
    description:
      "Review research papers, methods, contributions, and limitations.",
  },
  {
    name: "ppt-generation",
    description: "Create presentations from ideas and reference materials.",
  },
  {
    name: "frontend-design",
    description: "Build polished frontend interfaces.",
  },
  {
    name: "image-generation",
    description: "Generate images from a written description.",
  },
  {
    name: "podcast-generation",
    description: "Create podcast scripts and audio.",
  },
  {
    name: "skill-creator",
    description: "Create reusable skills for new tasks.",
  },
  {
    name: "skill-reviewer",
    description: "Review skill quality and report potential issues.",
  },
].map((skill) => ({
  ...skill,
  category: "public",
  enabled: true,
  license: "MIT",
}));

async function mockCatalog(page: Page, locale = "en-US") {
  mockLangGraphAPI(page, { skills, threads: [] });
  await page.route("**/api/mcp/config", (route) =>
    route.fulfill({
      json: {
        mcp_servers: {
          GitHub: {
            capability: {
              id: "fixture-github",
              plugin_id: "github",
              version: "1",
            },
            description:
              locale === "zh-CN"
                ? "搜索代码与仓库，查看 Issue 和 Pull Request，协助推进开发工作。"
                : "Search code and repositories, review issues, and work with pull requests.",
            enabled: true,
            type: "http",
            url: "https://example.test/github",
          },
          Notion: {
            capability: {
              id: "fixture-notion",
              plugin_id: "notion",
              version: "1",
            },
            description:
              locale === "zh-CN"
                ? "搜索工作空间里的笔记和文档，整理资料，创建新的页面。"
                : "Search workspace notes and documents, organize knowledge, and create pages.",
            enabled: true,
            type: "http",
            url: "https://example.test/notion",
          },
          "Brave Search": {
            capability: {
              id: "fixture-brave-search",
              plugin_id: "brave-search",
              version: "1",
            },
            description:
              locale === "zh-CN"
                ? "搜索互联网上的信息，为研究、写作和决策补充最新资料。"
                : "Search the web for information to support research, writing, and decisions.",
            enabled: true,
            command: "example-search",
          },
          Filesystem: {
            capability: {
              id: "fixture-filesystem",
              plugin_id: "filesystem",
              version: "1",
            },
            description:
              locale === "zh-CN"
                ? "访问已授权的文件夹，读取文件内容并整理本地工作资料。"
                : "Access authorized folders, read files, and organize local working documents.",
            enabled: true,
            command: "example-files",
          },
          PostgreSQL: {
            capability: {
              id: "fixture-postgres",
              plugin_id: "database",
              version: "1",
            },
            description:
              locale === "zh-CN"
                ? "查询数据库中的业务数据，探索表结构，辅助数据分析。"
                : "Query business data, explore database schemas, and support analysis.",
            enabled: false,
            command: "example-database",
          },
        },
      },
    }),
  );
}

async function screenshot(page: Page, name: string) {
  const directory = process.env.CAPABILITY_SCREENSHOT_DIR;
  if (!directory) return;
  await mkdir(directory, { recursive: true });
  await page.screenshot({ path: path.join(directory, name), fullPage: true });
}

test("catalog navigation, search, details, and migrated settings", async ({
  page,
  baseURL,
}) => {
  test.setTimeout(90_000);
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.setViewportSize({ width: 1512, height: 1060 });
  await page
    .context()
    .addCookies([{ name: "locale", value: "zh-CN", url: baseURL! }]);
  await mockCatalog(page, "zh-CN");
  await page.goto("/workspace/capabilities");
  await expect(
    page.getByRole("heading", { name: "能力中心", exact: true }),
  ).toBeVisible();
  await expect(page.locator("article")).toHaveCount(15);
  await expect(page.locator("a[href='/workspace/capabilities']")).toBeVisible();
  await screenshot(page, "capability-center-plugins.png");

  await page
    .getByRole("textbox", { name: "搜索插件名称或用途" })
    .fill("Notion");
  await expect(page.locator("article")).toHaveCount(1);
  await page.getByRole("button", { name: "编辑 Notion", exact: true }).click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await expect(
    page.getByRole("textbox", { name: "MCP 服务器 JSON 定义" }),
  ).toHaveValue(/example.test\/notion/);
  await page.keyboard.press("Escape");
  await page.getByRole("tab", { name: "技能", exact: true }).click();
  await expect(page.locator("article")).toHaveCount(9);
  await expect(
    page.getByRole("button", { name: "查看详情 深度研究", exact: true }),
  ).toBeVisible();
  await page.setViewportSize({ width: 1512, height: 1270 });
  await screenshot(page, "capability-center-skills.png");
  await page
    .getByRole("button", { name: "查看详情 深度研究", exact: true })
    .click();
  await expect(
    page.getByRole("dialog").getByText(skills[0]!.description),
  ).toBeVisible();
  await page.keyboard.press("Escape");
  await page.getByRole("tab", { name: "社区", exact: true }).click();
  await expect(page.getByText("从社区带来新的技能")).toBeVisible();

  await page.goto("/workspace/capabilities?settings=appearance");
  const settings = page.getByRole("dialog", { name: "设置", exact: true });
  await expect(settings).toBeVisible();
  for (const name of ["工具", "集成", "技能"]) {
    await expect(
      settings.getByRole("button", { name, exact: true }),
    ).toHaveCount(0);
  }
  expect(errors).toEqual([]);
});

test("the skills catalog fits a mobile viewport", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await mockCatalog(page);
  await page.goto("/workspace/capabilities?tab=skills");
  await expect(page).toHaveURL(/workspace\/capabilities\?tab=skills/);
  await expect(page.locator("article")).toHaveCount(9);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page
    .getByRole("button", { name: "Toggle Sidebar", exact: true })
    .click();
  await expect(
    page.getByRole("link", { name: "Capability Center" }),
  ).toBeVisible();
});

test("MCP access errors preserve the independently available catalog", async ({
  page,
}) => {
  await mockCatalog(page);
  await page.route("**/api/mcp/config", (route) =>
    route.fulfill({ status: 403, json: { detail: "Admin only" } }),
  );
  await page.goto("/workspace/capabilities");
  await expect(
    page.getByRole("alert").filter({ hasText: "Admin privileges" }),
  ).toBeVisible();
  await expect(
    page.locator("article").filter({ hasText: "Feishu group notifications" }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Add MCP plugin" }),
  ).toHaveCount(0);
});

test("Community search gives feedback and clearing it restores import guidance", async ({
  page,
}) => {
  await mockCatalog(page);
  await page.goto("/workspace/capabilities?tab=skills");
  await expect(page.locator("article")).toHaveCount(9);
  const search = page.getByRole("textbox", {
    name: "Search skills by name or purpose",
  });
  await search.fill("nonexistent-query");
  await expect(
    page.getByText("No matches found", { exact: true }),
  ).toBeVisible();
  await page.getByRole("tab", { name: "Community", exact: true }).click();
  await expect(search).toHaveValue("nonexistent-query");
  await expect(
    page.getByText("No matches found", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("Bring a skill from the community", { exact: true }),
  ).toHaveCount(0);
  await search.fill("   ");
  await expect(
    page.getByText("Bring a skill from the community", { exact: true }),
  ).toBeVisible();
  await search.fill("");
  await page.getByRole("tab", { name: "Built-in", exact: true }).click();
  await expect(page.locator("article")).toHaveCount(9);
});

test("plugin filters remain usable after an MCP refetch fails", async ({
  page,
}) => {
  await mockCatalog(page);
  let failRead = false;
  await page.route("**/api/mcp/config", async (route) => {
    if (route.request().method() === "PATCH") {
      failRead = true;
      return route.fulfill({ json: { mcp_servers: {} } });
    }
    if (failRead)
      return route.fulfill({ status: 403, json: { detail: "Admin only" } });
    return route.fallback();
  });
  await page.goto("/workspace/capabilities");
  await expect(page.locator("article")).toHaveCount(15);
  const installed = page.getByRole("tab", { name: "Installed", exact: true });
  await installed.click();
  await expect(page.locator("article")).toHaveCount(5);
  await page
    .getByRole("switch", { name: "Enabled GitHub", exact: true })
    .click();
  // Next's route announcer is also role="alert"; match the MCP error itself.
  await expect(
    page.getByRole("alert").filter({ hasText: "Admin privileges" }),
  ).toBeVisible();
  await expect(installed).toHaveAttribute("aria-selected", "true");
  await expect(
    page.getByRole("button", { name: "Add MCP plugin" }),
  ).toHaveCount(0);
  await page.getByRole("tab", { name: "All plugins", exact: true }).click();
  await expect(
    page.locator("article").filter({ hasText: "HubSpot CRM" }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Configure HubSpot CRM", exact: true })
    .click();
  await expect(page.getByRole("dialog")).toBeVisible();
});

test("plugin categories, setup guides, and installed state remain distinct", async ({
  page,
  baseURL,
}) => {
  await page.setViewportSize({ width: 1512, height: 1700 });
  await page
    .context()
    .addCookies([{ name: "locale", value: "zh-CN", url: baseURL! }]);
  await mockCatalog(page, "zh-CN");
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/workspace/capabilities");
  await expect(page.locator("article")).toHaveCount(15);
  for (const name of [
    "办公协作",
    "文档与知识",
    "搜索与研究",
    "业务与数据",
    "研发与运维",
  ]) {
    await expect(
      page.getByRole("heading", { name, exact: true }),
    ).toBeVisible();
  }
  await screenshot(page, "capability-catalog-zh.png");
  await page.getByRole("button", { name: "办公协作", exact: true }).click();
  await expect(page.locator("article")).toHaveCount(1);
  await expect(
    page.locator("article").filter({ hasText: "GitHub" }),
  ).toHaveCount(0);
  await page
    .getByRole("button", { name: "配置 飞书群通知", exact: true })
    .click();
  await expect(page.getByRole("dialog")).toContainText("自定义机器人");
  await expect(page.getByRole("dialog").getByRole("link")).toHaveAttribute(
    "href",
    "https://open.feishu.cn/document/client-docs/bot-v3/add-custom-bot",
  );
  await page.keyboard.press("Escape");
  await page.getByRole("button", { name: "全部分类", exact: true }).click();
  await page
    .getByRole("textbox", { name: "搜索插件名称或用途" })
    .fill("firecrawl");
  await expect(page.locator("article")).toHaveCount(1);
  await expect(page.locator("article")).toContainText("未配置");
  await page
    .getByRole("button", { name: "配置 Firecrawl", exact: true })
    .click();
  await expect(page.getByRole("dialog")).toContainText("Firecrawl");
  await expect(page.getByRole("dialog").getByLabel("API Key")).toBeVisible();
  await screenshot(page, "capability-catalog-detail-zh.png");
  await page.keyboard.press("Escape");
  await page.getByRole("tab", { name: "已安装", exact: true }).click();
  await expect(page.locator("article")).toHaveCount(0);
  await expect(
    page.getByText("没有找到匹配的内容", { exact: true }),
  ).toBeVisible();
  await page.getByRole("textbox", { name: "搜索插件名称或用途" }).fill("");
  await expect(page.locator("article")).toHaveCount(5);
  await page.getByRole("tab", { name: "全部插件", exact: true }).click();
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await screenshot(page, "capability-catalog-mobile.png");
  expect(errors).toEqual([]);
});

test("English plugin catalog preview", async ({ page }) => {
  await page.setViewportSize({ width: 1512, height: 1850 });
  await mockCatalog(page);
  await page.goto("/workspace/capabilities");
  await expect(page.locator("article")).toHaveCount(15);
  await screenshot(page, "capability-catalog-en.png");
});

test("manifest installation saves through the adapter and refreshes the catalog", async ({
  page,
}) => {
  mockLangGraphAPI(page);
  let installed: Record<string, unknown> | null = null;
  let submission: Record<string, unknown> | null = null;
  const checks: unknown[] = [];
  await page.route("**/api/mcp/config", (route) =>
    route.fulfill({
      json: { mcp_servers: installed ? { "team-code": installed } : {} },
    }),
  );
  await page.route("**/api/capabilities/installations", (route) => {
    submission = route.request().postDataJSON() as Record<string, unknown>;
    // The remote adapter owns the endpoint; the stored server never echoes
    // the token back.
    installed = {
      enabled: true,
      type: "http",
      url: "https://api.githubcopilot.com/mcp/readonly",
      headers: { Authorization: "***" },
      capability: { id: "stable-github", plugin_id: "github", version: "2" },
    };
    return route.fulfill({ json: { items: [], can_manage: true } });
  });
  await page.route("**/api/capabilities/connections/check", (route) => {
    const body = route.request().postDataJSON() as { name: string };
    checks.push(body);
    return route.fulfill({
      json: {
        name: body.name,
        ok: true,
        code: "ok",
        tool_count: 12,
        tools: ["get_me", "search_code", "list_issues"],
        detail: null,
      },
    });
  });
  await page.goto("/workspace/capabilities");
  await page
    .getByRole("textbox", { name: "Search plugins by name or purpose" })
    .fill("github");
  await page
    .getByRole("button", { name: "Configure GitHub", exact: true })
    .click();
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByLabel("Server URL")).toHaveCount(0);
  await dialog.getByLabel("Connection name").fill("team-code");
  const token = dialog.getByLabel("Personal access token");
  await expect(token).toHaveAttribute("type", "password");
  await expect(token).toHaveAttribute("autocomplete", "new-password");
  await token.fill("fixture-only-token");
  await dialog.getByRole("button", { name: "Save configuration" }).click();
  // The dialog stays open until the post-save connection check reports.
  await expect(dialog.getByText("Connected. Tools found: 12")).toBeVisible();
  await expect(
    dialog.getByText("Tools: get_me, search_code, list_issues"),
  ).toBeVisible();
  expect(submission).toEqual({
    plugin_id: "github",
    name: "team-code",
    configuration: { token: "fixture-only-token" },
  });
  expect(checks).toEqual([{ name: "team-code" }]);
  await dialog.getByRole("button", { name: "Done", exact: true }).click();
  await expect(dialog).toHaveCount(0);
  await page
    .getByRole("textbox", { name: "Search plugins by name or purpose" })
    .fill("");
  await expect(
    page.getByRole("button", { name: "Edit team-code", exact: true }),
  ).toBeVisible();
  const row = page.locator("article").filter({ hasText: "team-code" });
  await expect(row).toHaveCount(1);
  await row
    .getByRole("button", { name: "Test connection team-code", exact: true })
    .click();
  await expect(row.getByText("Connected. Tools found: 12")).toBeVisible();
  expect(checks).toHaveLength(2);
  await page.reload();
  await expect(
    page.getByRole("button", { name: "Edit team-code", exact: true }),
  ).toBeVisible();
});

test("a failed post-save check explains the reason and how to reconfigure", async ({
  page,
  baseURL,
}) => {
  await page
    .context()
    .addCookies([{ name: "locale", value: "zh-CN", url: baseURL! }]);
  mockLangGraphAPI(page);
  await page.route("**/api/capabilities/installations", (route) =>
    route.fulfill({ json: { items: [], can_manage: true } }),
  );
  await page.route("**/api/capabilities/connections/check", (route) =>
    route.fulfill({
      json: {
        name: "openviking",
        ok: false,
        code: "unreachable",
        tool_count: 0,
        tools: [],
        detail: "Connection refused",
      },
    }),
  );
  await page.goto("/workspace/capabilities?plugin=openviking");
  const dialog = page.getByRole("dialog");
  const url = dialog.getByLabel("服务地址");
  await expect(url).toHaveAttribute(
    "placeholder",
    "https://mcp.example.com/mcp",
  );
  await expect(url).toHaveAttribute("autocomplete", "off");
  await expect(dialog.getByLabel("授权请求头（可选）")).toHaveAttribute(
    "placeholder",
    "Bearer <token>",
  );
  await url.fill("name@company.com");
  await dialog.getByRole("button", { name: "保存配置" }).click();
  await expect(dialog.getByRole("alert")).toHaveText(
    "请输入 HTTP 或 HTTPS 服务地址。",
  );
  await url.fill("https://mcp.example.test/mcp");
  await dialog.getByRole("button", { name: "保存配置" }).click();
  const alert = dialog.getByRole("alert");
  await expect(alert).toContainText("无法连接到服务地址");
  await expect(alert).toContainText("Connection refused");
  await expect(alert).toContainText("删除此条目后重新配置");
  await expect(dialog.getByRole("button", { name: "完成" })).toBeVisible();
});

test("hidden manifests stay out of discovery and deep links", async ({
  page,
}) => {
  mockLangGraphAPI(page);
  await page.goto("/workspace/capabilities");
  await expect(page.locator("article")).toHaveCount(12);
  for (const name of [
    "Lark / Feishu",
    "DingTalk",
    "WeCom",
    "Tencent Docs",
    "Notion",
    "Browser",
  ]) {
    await expect(page.locator("article").filter({ hasText: name })).toHaveCount(
      0,
    );
  }
  await page.goto("/workspace/capabilities?plugin=dingtalk");
  await expect(page.locator("article")).toHaveCount(12);
  await expect(page.getByRole("dialog")).toHaveCount(0);
});

test("native capabilities report deployment status and credential-free plugins enable directly", async ({
  page,
}) => {
  mockLangGraphAPI(page);
  await page.route("**/api/capabilities/installations/native", (route) =>
    route.fulfill({
      json: {
        can_manage: true,
        items: [
          {
            id: "native:web-search",
            plugin_id: "web-search",
            adapter: "native",
            name: "Web search",
            reference: "web-search",
            installed: true,
            enabled: true,
            scope: "deployment",
            auth_status: "not_required",
            health: "unknown",
          },
        ],
      },
    }),
  );
  let submission: Record<string, unknown> | undefined;
  await page.route("**/api/capabilities/installations", (route) => {
    submission = route.request().postDataJSON() as Record<string, unknown>;
    return route.fulfill({ json: { items: [], can_manage: true } });
  });
  await page.goto("/workspace/capabilities");
  // Exa's description also mentions web search; find rows by their action.
  const row = (name: string) =>
    page.locator("article").filter({
      has: page.getByRole("button", { name: `View ${name}`, exact: true }),
    });
  await expect(row("Web search")).toContainText("Enabled");
  await expect(row("Web reader")).toContainText("Disabled");
  await row("Web search")
    .getByRole("button", { name: "View Web search", exact: true })
    .click();
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByRole("status")).toHaveText(
    "Enabled in this deployment",
  );
  await expect(dialog.getByRole("textbox")).toHaveCount(0);
  await expect(dialog.getByRole("button", { name: /Save|Enable/ })).toHaveCount(
    0,
  );
  await page.keyboard.press("Escape");
  await page
    .getByRole("button", { name: "View Web reader", exact: true })
    .click();
  await expect(dialog.getByRole("status")).toContainText(
    "Contact your administrator",
  );
  await page.keyboard.press("Escape");

  await page
    .getByRole("button", { name: "Configure Google Docs", exact: true })
    .click();
  await expect(dialog.getByRole("textbox")).toHaveCount(0);
  await dialog.getByRole("button", { name: "Enable", exact: true }).click();
  await expect(dialog.getByText("Connected. Tools found: 2")).toBeVisible();
  expect(submission).toEqual({
    plugin_id: "google-docs",
    name: "google-docs",
    configuration: {},
  });
});

test("agent selection saves explicit plugin IDs and an empty skill list", async ({
  page,
}) => {
  mockLangGraphAPI(page, {
    agents: [{ name: "analyst", description: "Analyze reports" }],
    skills,
  });
  await page.route("**/api/capabilities/installations/mcp", (route) =>
    route.fulfill({
      json: {
        can_manage: false,
        items: [
          {
            id: "stable-github",
            name: "Team GitHub",
            adapter: "mcp",
            reference: "team-code",
            installed: true,
            enabled: true,
          },
        ],
      },
    }),
  );
  let selection: Record<string, unknown> | undefined;
  await page.route("**/api/agents", (route) =>
    route.fulfill({ json: { agents: [{ name: "analyst", ...selection }] } }),
  );
  await page.route("**/api/agents/analyst", (route) => {
    if (route.request().method() === "PUT")
      selection = route.request().postDataJSON() as Record<string, unknown>;
    return route.fulfill({ json: { name: "analyst", ...selection } });
  });
  await page.goto("/workspace/agents");
  await page.getByTitle("Agent settings", { exact: true }).click();
  const dialog = page.getByRole("dialog");
  await dialog
    .locator("summary")
    .filter({ hasText: "Plugins and skills" })
    .click();
  const plugins = dialog.getByRole("group", {
    name: "MCP plugins",
    exact: true,
  });
  await plugins.getByLabel("Use all enabled", { exact: true }).uncheck();
  await plugins.getByLabel("Team GitHub", { exact: true }).check();
  await dialog
    .getByRole("group", {
      name: "Skills (including integration skills)",
      exact: true,
    })
    .getByLabel("Use all enabled", { exact: true })
    .uncheck();
  await screenshot(page, "agent-capability-selection-en.png");
  await dialog.getByRole("button", { name: "Save", exact: true }).click();
  await expect(dialog).toHaveCount(0);
  expect(selection).toMatchObject({
    mcp_plugins: ["stable-github"],
    skills: [],
  });
  await page.getByTitle("Agent settings", { exact: true }).click();
  await dialog
    .locator("summary")
    .filter({ hasText: "Plugins and skills" })
    .click();
  await expect(
    plugins.getByLabel("Team GitHub", { exact: true }),
  ).toBeChecked();
});

test("renaming an Agent preserves concurrently updated plugin and skill selections", async ({
  page,
}) => {
  mockLangGraphAPI(page);
  let saved = {
    name: "analyst",
    display_name: "Analyst",
    mcp_plugins: ["old-plugin"],
    skills: ["old-skill"],
  };
  let request: Record<string, unknown> | undefined;
  await page.route("**/api/agents", (route) =>
    route.fulfill({ json: { agents: [saved] } }),
  );
  await page.route("**/api/agents/analyst", (route) => {
    if (route.request().method() === "PUT") {
      request = route.request().postDataJSON() as Record<string, unknown>;
      saved = { ...saved, ...request };
    }
    return route.fulfill({ json: saved });
  });
  await page.goto("/workspace/agents");
  await page.getByTitle("Agent settings", { exact: true }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByLabel("Display name")).toHaveValue("Analyst");
  // Another editor saves capability selections after this dialog has opened.
  saved = { ...saved, mcp_plugins: ["new-plugin"], skills: ["new-skill"] };
  await dialog.getByLabel("Display name").fill("Renamed analyst");
  await dialog.getByRole("button", { name: "Save", exact: true }).click();
  await expect(dialog).toHaveCount(0);
  expect(request).not.toHaveProperty("mcp_plugins");
  expect(request).not.toHaveProperty("skills");
  expect(saved).toMatchObject({
    display_name: "Renamed analyst",
    mcp_plugins: ["new-plugin"],
    skills: ["new-skill"],
  });
});
