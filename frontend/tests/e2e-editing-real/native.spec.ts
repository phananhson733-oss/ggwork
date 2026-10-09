import { createHash } from "node:crypto";
import { readFileSync, writeFileSync } from "node:fs";
import path from "node:path";

import { expect, test, type Page, type TestInfo } from "@playwright/test";

import type { EditingTask } from "../../src/core/editing/types";

// No page.route interception: this suite requires an isolated real Gateway,
// admitted cloud text planner, paired native worker and synthetic source files.
type QA = {
  email: string;
  password: string;
  deviceId: string;
  directoryGrant: string;
  directoryPath: string;
  receiveGrant: string;
  sourceFiles: { path: string; episode: number }[];
  existingTaskId: string;
  completedTaskId: string;
  profile: string;
  language: string;
  durationSeconds: number;
};
const qa = JSON.parse(
  readFileSync(process.env.EDITING_QA_CONFIG!, "utf8"),
) as QA;

async function saveEvidence(info: TestInfo, name: string, data: unknown) {
  const evidencePath = info.outputPath(`${name}.json`);
  writeFileSync(evidencePath, JSON.stringify(data, null, 2));
  await info.attach(name, {
    path: evidencePath,
    contentType: "application/json",
  });
}

async function login(page: Page) {
  await page.goto("/login");
  await page.locator("#email").fill(qa.email);
  await page.locator("#password").fill(qa.password);
  const response = page.waitForResponse((r) =>
    r.url().endsWith("/api/v1/auth/login/local"),
  );
  await page.locator("form button[type=submit]").click();
  expect((await response).status()).toBe(200);
  await expect(page).toHaveURL(/\/workspace/);
}

async function task(page: Page, id: string): Promise<EditingTask> {
  const response = await page.request.get(`/api/editing/tasks/${id}`);
  expect(response.status()).toBe(200);
  return response.json() as Promise<EditingTask>;
}

test("real device status and directory draft make no task mutations before Start", async ({
  page,
}, info) => {
  await login(page);
  const devices = await page.request.get("/api/editing/devices");
  expect(devices.status()).toBe(200);
  const data = (await devices.json()) as {
    items: { id: string; online: boolean; ready: boolean }[];
  };
  const device = data.items.find((item) => item.id === qa.deviceId);
  expect(device).toBeDefined();
  const mutations: string[] = [];
  page.on("request", (request) => {
    if (
      request.method() !== "GET" &&
      new URL(request.url()).pathname.startsWith("/api/editing/")
    )
      mutations.push(new URL(request.url()).pathname);
  });
  await page.goto("/workspace/editing/new?title=Synthetic%20directory%20draft");
  await page.getByLabel("选择执行设备").selectOption(qa.deviceId);
  await expect(
    page.getByText(
      device!.online
        ? device!.ready
          ? "Mac 已连接，剪辑环境已就绪"
          : "Mac 已连接，剪辑环境待检查"
        : "设备离线，请在 Mac 启动执行器",
      { exact: true },
    ),
  ).toBeVisible();
  await page
    .getByRole("combobox", { name: "已授权目录", exact: true })
    .selectOption(qa.directoryGrant);
  await page.getByLabel("授权目录内的相对文件夹").fill(qa.directoryPath);
  await page
    .getByRole("textbox", { name: "剪辑要求", exact: true })
    .fill("Preserve this draft without starting execution.");
  expect(mutations).toEqual([]);
  await expect(page).toHaveURL(/\/workspace\/editing\/new\?/);
  await page
    .locator("main.editing-workspace")
    .screenshot({ path: info.outputPath("directory-draft.png") });
});

test("real history and detail retain the same HTTP task identity", async ({
  page,
}, info) => {
  await login(page);
  const record = await task(page, qa.existingTaskId);
  await page.goto("/workspace/editing");
  const link = page.locator(`main a[href="/workspace/editing/${record.id}"]`);
  await expect(link).toHaveText(record.title);
  await link.click();
  await expect(page).toHaveURL(new RegExp(`/workspace/editing/${record.id}$`));
  await expect(
    page
      .locator("main")
      .getByRole("heading", { name: record.title, exact: true }),
  ).toBeVisible();
  await page.getByText("任务记录与连接说明", { exact: true }).click();
  await expect(
    page.getByText(`任务标识：${record.id}`, { exact: true }),
  ).toBeVisible();
  await page
    .locator("main.editing-workspace")
    .screenshot({ path: info.outputPath("shared-task-identity.png") });
  await saveEvidence(info, "task-identity", {
    taskId: record.id,
    status: record.status,
    stage: record.stage,
    completedCount: record.completed_count,
  });
});

async function verifyDelivery(page: Page, id: string, info: TestInfo) {
  await expect
    .poll(async () => (await task(page, id)).status, {
      timeout: 240_000,
      intervals: [1000, 2000, 5000],
    })
    .toBe("completed");
  const record = await task(page, id);
  expect(record.id).toBe(id);
  expect(record.manifest_frozen).toBe(true);
  expect(
    record.source_manifest?.files.every((file) => file.state === "verified"),
  ).toBe(true);
  expect(record.completed_count).toBe(record.requested_count);
  await page.goto(`/workspace/editing/${id}`);
  const main = page.locator("main.editing-workspace");
  await expect(main.getByRole("status")).toContainText("已完成");
  await main.getByRole("button", { name: "检查成片访问" }).first().click();
  await main.getByRole("button", { name: "预览成片" }).first().click();
  const video = main.locator("video").first();
  await expect
    .poll(() => video.evaluate((v: HTMLVideoElement) => v.readyState))
    .toBeGreaterThanOrEqual(2);
  await video.evaluate(async (v: HTMLVideoElement) => {
    v.muted = true;
    await v.play();
  });
  await expect
    .poll(() => video.evaluate((v: HTMLVideoElement) => v.currentTime))
    .toBeGreaterThan(0.5);
  const media = await video.evaluate((v: HTMLVideoElement) => ({
    duration: v.duration,
    width: v.videoWidth,
    height: v.videoHeight,
    currentTime: v.currentTime,
    error: v.error?.code ?? null,
  }));
  expect(media.width).toBeGreaterThan(0);
  expect(media.height).toBeGreaterThan(0);
  expect(media.error).toBeNull();
  await video.evaluate((v: HTMLVideoElement) => v.pause());
  await video.screenshot({ path: info.outputPath("decoded-video-frame.png") });
  const output = record.outputs.find((value) => value.status === "completed")!;
  const content = `/api/editing/tasks/${id}/outputs/${output.id}/content`;
  const range = await page.request.get(content, {
    headers: { Range: "bytes=0-1023" },
  });
  expect(range.status()).toBe(206);
  expect(range.headers()["content-range"]).toBe(
    `bytes 0-1023/${output.result!.size_bytes}`,
  );
  expect((await range.body()).length).toBe(1024);
  const downloadEvent = page.waitForEvent("download");
  await main.getByRole("link", { name: "下载成片" }).first().click();
  const download = await downloadEvent;
  const outputPath = info.outputPath("native-output.mp4");
  await download.saveAs(outputPath);
  expect(await download.failure()).toBeNull();
  const bytes = readFileSync(outputPath);
  expect(bytes.length).toBe(output.result!.size_bytes);
  const digest = createHash("sha256").update(bytes).digest("hex");
  const expected = output.result as unknown as { sha256: string };
  expect(digest).toBe(expected.sha256);
  await main.screenshot({ path: info.outputPath("completed-playback.png") });
  await saveEvidence(info, "verified-delivery", {
    taskId: id,
    outputId: output.id,
    artifactId: output.result!.artifact_id,
    bytes: bytes.length,
    sha256: digest,
    rangeStatus: range.status(),
    media,
  });
  return record;
}

test("real native directory task decodes, downloads completely and retains original version references", async ({
  page,
}, info) => {
  await login(page);
  expect(
    qa.completedTaskId,
    "A genuinely completed native directory task is required",
  ).toBeTruthy();
  const original = await verifyDelivery(page, qa.completedTaskId, info);
  await page.getByRole("button", { name: "调整要求，创建新版本" }).click();
  await expect(page.getByLabel("提供素材方式")).toHaveValue("existing");
  await expect(
    page.getByRole("link", { name: "查看原版", exact: true }),
  ).toHaveAttribute("href", `/workspace/editing/${original.id}`);
  await page
    .getByRole("textbox", { name: "剪辑要求", exact: true })
    .fill("Revision draft only; retain original verified media.");
  expect((await task(page, original.id)).outputs).toEqual(original.outputs);
  await page
    .locator("main.editing-workspace")
    .screenshot({ path: info.outputPath("revision-draft.png") });
});

test("real submitted file stays a draft until Start then native ACK, cloud planning and verified MP4 delivery", async ({
  page,
}, info) => {
  await login(page);
  await page.goto(
    "/workspace/editing/new?title=Synthetic%20browser%20acceptance",
  );
  await page.getByLabel("选择执行设备").selectOption(qa.deviceId);
  await expect(page.getByText("Mac 已连接，剪辑环境已就绪")).toBeVisible();
  await page.getByLabel("提供素材方式").selectOption("files");
  await page
    .getByRole("combobox", { name: "已授权目录", exact: true })
    .selectOption(qa.receiveGrant);
  const mutations: string[] = [];
  page.on("request", (request) => {
    if (
      request.method() !== "GET" &&
      new URL(request.url()).pathname.startsWith("/api/editing/")
    )
      mutations.push(request.method() + " " + new URL(request.url()).pathname);
  });
  await page
    .getByLabel("选择本次素材")
    .setInputFiles(qa.sourceFiles.map((file) => file.path));
  for (const file of qa.sourceFiles)
    await page
      .getByLabel(`${path.basename(file.path)} 集号`, { exact: true })
      .fill(String(file.episode));
  await page
    .getByRole("combobox", { name: "剪辑模式", exact: true })
    .selectOption(qa.profile);
  await page
    .getByRole("textbox", { name: "剪辑要求", exact: true })
    .fill(
      "Create one coherent short clip preserving the original spoken dialogue. Only use the supplied transcript and source ranges.",
    );
  await page.getByLabel("每条时长（秒）").fill(String(qa.durationSeconds));
  await page.getByLabel("素材语言", { exact: true }).fill(qa.language);
  await page
    .getByRole("combobox", { name: "画幅", exact: true })
    .selectOption("16:9");
  expect(mutations).toEqual([]);
  await page
    .locator("main.editing-workspace")
    .screenshot({ path: info.outputPath("selected-draft.png") });
  const createdResponse = page.waitForResponse(
    (r) =>
      new URL(r.url()).pathname === "/api/editing/tasks" &&
      r.request().method() === "POST",
  );
  await page.getByRole("button", { name: "开始剪辑", exact: true }).click();
  const created = await createdResponse;
  expect(created.ok()).toBe(true);
  const createdTask = (await created.json()) as EditingTask;
  // Preserve the accepted identity even if a later native/media assertion fails.
  // Operators can inspect this task without spending another planner call.
  await saveEvidence(info, "accepted-task", { taskId: createdTask.id });
  await expect(page).toHaveURL(
    new RegExp(`/workspace/editing/${createdTask.id}$`),
    { timeout: 90_000 },
  );
  expect(
    mutations.filter((request) => request === "POST /api/editing/tasks"),
  ).toHaveLength(1);
  expect(
    mutations.some((request) =>
      request.startsWith("PUT /api/editing/uploads/"),
    ),
  ).toBe(true);
  const delivered = await verifyDelivery(page, createdTask.id, info);
  expect(delivered.source_directory).toBeNull();
  expect(delivered.source_manifest?.files.map((file) => file.name)).toEqual(
    qa.sourceFiles.map((file) => path.basename(file.path)),
  );
});
