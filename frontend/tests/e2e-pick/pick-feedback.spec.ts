import { randomUUID } from "node:crypto";
import { readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";

import { expect, test } from "@playwright/test";

// Real authenticated Gateway + SQL + browser. Only the Feishu provider and model
// invocation are synthetic; private fixture launcher lives outside product code.
const fixture = process.env.PICK_FEEDBACK_E2E_HOME;
test("synthetic source crosses real sync, persistence, candidate notes and UI", async ({
  page,
  context,
  baseURL,
}, info) => {
  test.skip(
    !fixture,
    "Requires an isolated synthetic-provider Gateway fixture",
  );
  expect(["localhost", "127.0.0.1"]).toContain(new URL(baseURL!).hostname);
  const credentials = JSON.parse(
    readFileSync(join(fixture!, "credentials.json"), "utf8"),
  ) as { email: string; password: string };
  const state = (
    mode: string,
    views = 150,
    mapping_mode:
      | "confirmed"
      | "manual_unverified"
      | "pending_master" = "confirmed",
  ) =>
    writeFileSync(
      join(fixture!, "state.json"),
      JSON.stringify({ mode, views, mapping_mode }),
    );
  state("ok");
  await expect
    .poll(
      async () =>
        (await context.request.get("/api/v1/auth/setup-status")).status(),
      { timeout: 30000 },
    )
    .toBe(200);
  const login = await context.request.post("/api/v1/auth/login/local", {
    form: { username: credentials.email, password: credentials.password },
  });
  expect(login.status()).toBe(200);
  const csrf = (await context.cookies()).find(
    (c) => c.name === "csrf_token",
  )!.value;
  const headers = { "X-CSRF-Token": csrf };
  expect(
    (await context.request.post("/api/pick/e2e/enable", { headers })).status(),
  ).toBe(200);
  const status = async () =>
    (await context.request.get("/api/pick/feedback/status")).json();
  // A candidate uses the published feedback as it stands, so a changed source reaches it only through a refresh.
  const republish = async () => {
    const before = (await status()).current?.id;
    expect(
      (
        await context.request.post("/api/pick/feedback/sync", { headers })
      ).status(),
    ).toBe(202);
    await expect
      .poll(async () => (await status()).current?.id, { timeout: 20000 })
      .not.toBe(before);
  };
  await page.goto("/workspace/pick-data?tab=imports");
  const panel = page.getByRole("region", { name: "飞书运营反馈同步" });
  await expect(
    panel.getByRole("button", { name: "刷新飞书反馈" }),
  ).toBeVisible();
  await panel.getByRole("button", { name: "刷新飞书反馈" }).click();
  await expect
    .poll(async () => (await status()).current?.tables.length)
    .toBe(13);
  await expect(panel).toContainText("13 / 13", { timeout: 20000 });
  await panel.getByText("逐表状态", { exact: true }).click();
  await expect(panel.getByRole("listitem")).toHaveCount(13);
  await page.screenshot({
    path: info.outputPath("feedback-13-tables.png"),
    fullPage: true,
  });
  const selectionsBefore = await (
    await context.request.get("/api/pick/selections")
  ).json();
  const candidateResponse = await context.request.post(
    "/api/pick/e2e/candidate",
    { headers },
  );
  expect(candidateResponse.status()).toBe(200);
  const first = await candidateResponse.json();
  expect(first.feedback.status).toBe("ok");
  expect(
    await (await context.request.get("/api/pick/selections")).json(),
  ).toEqual(selectionsBefore);
  const notes = async (id: string) =>
    (await context.request.get(`/api/pick/results/${id}/notes`)).json();
  const frozen = await notes(first.id);
  const originalResult = await (
    await context.request.get(`/api/pick/results/${first.id}`)
  ).json();
  expect(frozen.feedback.contract_version).toBe("feedback-v1");
  expect(frozen.feedback.items[0].evidence_kind).toBe("direct");
  expect(frozen.feedback.items[0].metrics.identity_method).toBe(
    "confirmed_master",
  );
  expect(frozen.feedback.items[0].metrics.views_total).toBe("150");
  expect(frozen.feedback.items[0].revenue).toEqual([
    expect.objectContaining({
      source_lane: "cps_manual",
      grain: "drama",
      currency: "USD",
      metric: "commission",
      amount: "12.34",
    }),
  ]);
  expect(frozen.feedback.items[0].evidence_refs).toEqual(
    expect.arrayContaining([
      expect.objectContaining({ source_lane: "dramas", record_id: "drama-a" }),
      expect.objectContaining({
        source_lane: "cps_manual",
        record_id: "revenue-manual",
        attribution: "confirmed",
      }),
    ]),
  );
  // feedback-v3 reads no automatic CPS table, and nothing else stands in for it.
  expect(
    frozen.feedback.items[0].evidence_refs.map(
      (ref: { source_lane: string }) => ref.source_lane,
    ),
  ).not.toContain("cps_auto");
  expect(frozen.feedback.items[0].coverage.measured_posts).toBe(2);
  expect(frozen.feedback.items[0].coverage.missing_posts).toBe(1);
  expect((await context.request.get("/api/pick/selections")).ok()).toBeTruthy();
  const command = {
    request_id: randomUUID(),
    result_id: first.id,
    item_ids: [first.items[0].item_id],
    note: "Synthetic browser acceptance",
  };
  const saved = await context.request.post("/api/pick/selections", {
    headers,
    data: command,
  });
  expect(saved.status()).toBe(200);
  expect(
    (
      await context.request.post("/api/pick/selections", {
        headers,
        data: command,
      })
    ).status(),
  ).toBe(200);
  await page.goto("/workspace/picks");
  await page.getByRole("button", { name: "查看来源候选" }).last().click();
  const evidence = page.getByRole("region", { name: "运营反馈依据" }).last();
  await expect(evidence).toContainText("累计播放：150");
  await expect(evidence).toContainText("点赞：未知");
  await expect(evidence).toContainText(
    "CPS 手动明细 · 单剧 · 分成收益：12.34 USD",
  );
  await expect(evidence).toContainText("已测播放 2 条 · 缺失 1 条");
  await expect(
    page.getByRole("region", { name: "运营反馈版本" }).last(),
  ).toContainText("历史候选依据");
  await evidence.getByText(/核对飞书来源/).click();
  expect(await evidence.getByRole("link").count()).toBeGreaterThan(0);
  await expect(evidence.getByRole("link").first()).toHaveAttribute(
    "href",
    /^https:\/\/gengrowth\.feishu\.cn\/base\/OtnsbnRnwaLmnVsJByscTkFMntd\?table=.+&record=.+/,
  );
  await page.screenshot({
    path: info.outputPath("feedback-frozen-candidate.png"),
    fullPage: true,
  });
  state("ok", 900);
  // Without a refresh the next candidate still reads the version already published.
  const unrefreshed = await (
    await context.request.post("/api/pick/e2e/candidate", { headers })
  ).json();
  expect(unrefreshed.feedback.feedback_version_id).toBe(
    first.feedback.feedback_version_id,
  );
  await republish();
  const next = await (
    await context.request.post("/api/pick/e2e/candidate", { headers })
  ).json();
  expect(next.feedback.feedback_version_id).not.toBe(
    first.feedback.feedback_version_id,
  );
  expect((await notes(next.id)).feedback.items[0].metrics.views_total).toBe(
    "900",
  );
  expect((await notes(first.id)).feedback).toEqual(frozen.feedback);
  const oldResult = await (
    await context.request.get(`/api/pick/results/${first.id}`)
  ).json();
  expect(oldResult.items).toEqual(originalResult.items);
  expect(oldResult.conditions).toEqual(originalResult.conditions);
  expect(oldResult.run_status).toBe("success");
  await page.reload();
  await page.getByRole("button", { name: "查看来源候选" }).last().click();
  await expect(
    page.getByRole("region", { name: "运营反馈依据" }).last(),
  ).toContainText("累计播放：150");
  // A manual row whose grain is not stated as single-drama keeps its link but attributes nothing.
  state("ok", 900, "manual_unverified");
  await republish();
  const revokedResponse = await context.request.post(
    "/api/pick/e2e/candidate",
    { headers },
  );
  expect(revokedResponse.status()).toBe(200);
  const revoked = await revokedResponse.json();
  const revokedNotes = await notes(revoked.id);
  expect(revokedNotes.feedback.feedback_version_id).not.toBe(
    next.feedback.feedback_version_id,
  );
  expect(revokedNotes.feedback.items[0].metrics.identity_status).toBe(
    "confirmed",
  );
  expect(revokedNotes.feedback.items[0].metrics.views_total).toBe("900");
  expect(revokedNotes.feedback.items[0].revenue).toEqual([]);
  expect(revokedNotes.feedback.items[0].warnings).toContain(
    "revenue_grain_unconfirmed",
  );
  expect(revokedNotes.feedback.items[0].evidence_refs).toEqual(
    expect.arrayContaining([
      expect.objectContaining({
        source_lane: "cps_manual",
        record_id: "revenue-manual",
        attribution: "ambiguous",
      }),
    ]),
  );
  expect((await notes(first.id)).feedback).toEqual(frozen.feedback);

  state("ok", 900, "pending_master");
  await republish();
  const unmatchedResponse = await context.request.post(
    "/api/pick/e2e/candidate",
    { headers },
  );
  expect(unmatchedResponse.status()).toBe(200);
  const unmatched = await unmatchedResponse.json();
  const unmatchedNotes = await notes(unmatched.id);
  expect(unmatchedNotes.feedback.feedback_version_id).not.toBe(
    revokedNotes.feedback.feedback_version_id,
  );
  expect(unmatchedNotes.feedback.items[0].evidence_kind).toBe("unknown");
  expect(unmatchedNotes.feedback.items[0].metrics.identity_status).toBe(
    "ambiguous",
  );
  expect(unmatchedNotes.feedback.items[0].metrics.views_total).toBeNull();
  expect(unmatchedNotes.feedback.items[0].coverage.posts).toBe(0);
  expect(unmatchedNotes.feedback.items[0].revenue).toEqual([]);
  expect(unmatchedNotes.feedback.items[0].warnings).toContain(
    "master_identity_unconfirmed",
  );
  expect((await notes(first.id)).feedback).toEqual(frozen.feedback);
  const afterMappingChange = await (
    await context.request.get(`/api/pick/results/${first.id}`)
  ).json();
  expect(afterMappingChange.items).toEqual(originalResult.items);
  expect(afterMappingChange.conditions).toEqual(originalResult.conditions);
  await page.reload();
  await page.getByRole("button", { name: "查看来源候选" }).last().click();
  const historicalEvidence = page
    .getByRole("region", { name: "运营反馈依据" })
    .last();
  await expect(historicalEvidence).toContainText("累计播放：150");
  await expect(historicalEvidence).toContainText(
    "CPS 手动明细 · 单剧 · 分成收益：12.34 USD",
  );
  await page.screenshot({
    path: info.outputPath("feedback-mapping-change-frozen.png"),
    fullPage: true,
  });

  state("partial", 900);
  await page.goto("/workspace/pick-data?tab=imports");
  await panel.getByRole("button", { name: "刷新飞书反馈" }).click();
  await expect
    .poll(async () => (await status()).current?.source_quality)
    .toBe("partial");
  await expect(panel).toContainText("部分", { timeout: 20000 });
  const stableVersion = (await status()).current.id;
  state("auth");
  await panel.getByRole("button", { name: "刷新飞书反馈" }).click();
  await expect(panel.getByRole("link", { name: "连接飞书" })).toBeVisible({
    timeout: 20000,
  });
  expect((await status()).current.id).toBe(stableVersion);
  await page.screenshot({
    path: info.outputPath("feedback-auth-required.png"),
    fullPage: true,
  });
  state("error");
  await panel.getByRole("button", { name: "刷新飞书反馈" }).click();
  await expect
    .poll(async () => (await status()).last_run?.error_code)
    .toBe("incomplete");
  await expect(panel).toContainText("旧版本未被覆盖");
  expect((await status()).current.id).toBe(stableVersion);
  state("pending", 950);
  await panel.getByRole("button", { name: "刷新飞书反馈" }).click();
  await expect(
    panel.getByRole("button", { name: "反馈刷新中…" }),
  ).toBeDisabled();
  await page.screenshot({
    path: info.outputPath("feedback-pending.png"),
    fullPage: true,
  });
  await expect
    .poll(async () => (await status()).running, { timeout: 60000 })
    .toBeNull();
  state("ok", 950);
});
