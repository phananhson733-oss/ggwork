import { randomUUID } from "node:crypto";
import { chmodSync, existsSync, readFileSync } from "node:fs";
import { join } from "node:path";

import {
  expect,
  test,
  type APIRequestContext,
  type Page,
} from "@playwright/test";

import {
  appendAttempt,
  budgets,
  extractTools,
  finishAttempt,
  loadManifest,
  readPrivate,
  readVerified,
  reserveCase,
  sha256,
  verifyBatches,
  verifyOwner,
  writePrivate,
  type Document,
} from "./support/readiness-protocol";

const locked = loadManifest(process.env.PICK_READINESS_MANIFEST);
const { manifest, hash, base } = locked;
const runner = manifest.runner;
const secrets = [process.env.PICK_E2E_PASSWORD!];
const runWaitMs = budgets(manifest.runtime.run_timeout_seconds, 1).runWaitMs;
async function get(
  request: APIRequestContext,
  path: string,
): Promise<Document> {
  const response = await request.get(path);
  if (!response.ok())
    throw new Error(`Readiness read failed: HTTP ${response.status()}`);
  return response.json() as Promise<Document>;
}
function artifact(dir: string, name: string, value: unknown) {
  return writePrivate(join(dir, name), value, secrets);
}
function ref(
  prefix: string,
  entry: { evidence_file: string; evidence_sha256: string },
) {
  return {
    [`${prefix}_file`]: entry.evidence_file,
    [`${prefix}_sha256`]: entry.evidence_sha256,
  };
}
async function selectionsForOwner(request: APIRequestContext) {
  const response = await get(request, "/api/pick/selections");
  return response.selections.map((row: Document) => ({
    ...row,
    owner_id: runner.identity.owner_id,
  }));
}
async function preflight(request: APIRequestContext, step: Document) {
  if (loadManifest(locked.path).hash !== hash)
    throw new Error("Locked expectations changed");
  verifyOwner(await get(request, "/api/v1/auth/me"), runner.identity);
  const source = manifest.sources[step.source_key];
  for (const field of [
    "rows",
    "metadata",
    ...(source.knowledge_batch_id ? ["knowledge"] : []),
  ])
    readVerified(base, source, field);
  const currentSource =
    manifest.sources[step.current_source_key ?? step.source_key];
  const imports = await get(request, "/api/pick/imports");
  const sync = await get(request, "/api/pick/sync");
  verifyBatches(imports.batches, sync.current, currentSource);
  // Historical-card steps can name a different current source, but the old batch must still be readable.
  const old = imports.batches.find(
    (b: Document) => b.id === source.catalog_batch_id,
  );
  if (
    !old?.shared ||
    old.content_hash !== source.content_hash ||
    old.validation_json?.source !== "realshort"
  )
    throw new Error("UNVERIFIED_DATA_VERSION: frozen batch unavailable");
  return { imports, sync, verified_at: new Date().toISOString() };
}
async function browserChecks(page: Page, checks: Document[] = []) {
  const assertions: { name: string; passed: boolean }[] = [];
  for (const check of checks) {
    const locator = page.locator(check.selector);
    if (check.count !== undefined)
      await expect(locator).toHaveCount(check.count);
    else if (check.text !== undefined)
      await expect(locator).toContainText(check.text);
    else await expect(locator).toBeVisible();
    assertions.push({ name: check.name ?? check.selector, passed: true });
  }
  return assertions;
}
function canonical(tool: Document) {
  const raw = tool.response;
  if (
    raw.status === "rejected" ||
    raw.status === "posted_unavailable" ||
    raw.status === "catalog_unavailable"
  )
    return {
      outcome: "refusal",
      response: { ...raw, status: "refused", reason_code: raw.status },
    };
  if (tool.tool_name === "pick_query_candidates" && raw.id)
    return { outcome: "query_success", response: raw };
  if (
    tool.tool_name === "pick_count_candidates" &&
    typeof raw.total === "number"
  )
    return { outcome: "count_success", response: raw };
  if (tool.tool_name === "pick_get_drama_detail" && raw.item)
    return { outcome: "detail_success", response: { ...raw, ...raw.item } };
  if (tool.tool_name === "pick_prepare_selection" && raw.requires_confirmation)
    return { outcome: "prepare_success", response: raw };
  if (
    tool.tool_name === "pick_search_knowledge" &&
    Array.isArray(raw.documents)
  )
    return {
      outcome: "knowledge_success",
      response: { ...raw, citations: raw.documents },
    };
  return { outcome: "unclassified", response: raw };
}

for (const item of manifest.cases as Document[]) {
  test(item.case_id, async ({ page, context }) => {
    test.setTimeout(
      budgets(manifest.runtime.run_timeout_seconds, item.planned_max_runs)
        .caseTimeoutMs,
    );
    const login = await context.request.post("/api/v1/auth/login/local", {
      form: {
        username: process.env.PICK_E2E_EMAIL!,
        password: process.env.PICK_E2E_PASSWORD!,
      },
    });
    if (!login.ok()) throw new Error(`QA login failed: HTTP ${login.status()}`);
    verifyOwner(await get(context.request, "/api/v1/auth/me"), runner.identity);
    const steps: Document[] = item.steps ?? [item];
    await preflight(context.request, steps[0]!);
    const reservation = reserveCase(
      runner.ledger_file,
      item.case_id,
      item.planned_max_runs,
      hash,
      steps.length,
    );
    const dir = join(runner.evidence_dir, reservation, randomUUID());
    const capturePath = join(runner.evidence_dir, `captures-${hash}.json`);
    const captures: Document = existsSync(capturePath)
      ? JSON.parse(readPrivate(capturePath).toString())
      : {
          spec_version: manifest.spec_version,
          expectations_sha256: hash,
          started_at: new Date().toISOString(),
          states: {},
          attempts: [],
          records: [],
        };
    if (captures.expectations_sha256 !== hash)
      throw new Error("Capture expectations mismatch");
    const results = new Map<string, Document>();
    const parents = new Map<string, string | null>();
    let threadId = item.thread_id ?? "";
    let active: Document | null = null;
    let routeError: Error | null = null;
    const persist = () => {
      const ledger = JSON.parse(
        readPrivate(runner.ledger_file).toString(),
      ) as Document;
      captures.attempts = ledger.attempts
        .filter((a: Document) =>
          ledger.reservations.some(
            (r: Document) =>
              r.id === a.reservation_id && r.manifest_sha256 === hash,
          ),
        )
        .map(
          ({
            case_id,
            step_id,
            attempt_id,
            run_id,
            status,
            started_at,
            ended_at,
            tool_call_ids,
          }: Document) => ({
            case_id,
            step_id,
            attempt_id,
            run_id,
            status,
            started_at,
            ended_at,
            tool_call_ids,
          }),
        );
      Object.assign(
        captures,
        ref("run_ledger", artifact(dir, "run-ledger.json", captures.attempts)),
      );
      writePrivate(capturePath, captures, secrets);
    };
    // Intercept before dispatch so refresh/regenerate cannot silently consume an uncounted run.
    await page.route(
      /\/api\/(?:langgraph\/)?threads\/[^/]+\/runs\/stream$/,
      async (route) => {
        try {
          if (route.request().method() !== "POST")
            return await route.continue();
          if (!active || active.dispatched)
            throw new Error("Unexpected new run outside reserved action");
          const payload = route.request().postDataJSON();
          const actualContext = payload.context ?? {};
          for (const [key, value] of Object.entries(
            runner.runtime_context ?? {},
          ))
            if ((actualContext[key] ?? null) !== value)
              throw new Error(
                "Submitted model context differs from verified runtime",
              );
          const bound = actualContext.pick_reference?.result_id ?? null;
          if (
            active.expected_bound !== undefined &&
            bound !== active.expected_bound
          )
            throw new Error("Submitted reference differs from locked action");
          active.bound_result_id = bound;
          active.request = payload;
          active.dispatched = true;
          threadId = new URL(route.request().url()).pathname
            .split("/threads/")[1]!
            .split("/")[0]!;
          appendAttempt(runner.ledger_file, reservation, {
            case_id: item.case_id,
            step_id: active.step_id,
            attempt_id: active.attempt_id,
            run_id: `unresolved:${active.attempt_id}`,
            status: "submitted",
            started_at: active.started_at,
            ended_at: null,
            tool_call_ids: [],
          });
          persist();
          artifact(dir, `${active.attempt_id}-request.json`, payload);
          await route.continue();
        } catch (error) {
          routeError =
            error instanceof Error ? error : new Error("Run dispatch refused");
          await route.abort("blockedbyclient");
        }
      },
    );
    // Imports and sync are forbidden throughout remote readiness, even if an action navigates incorrectly.
    await page.route(/\/api\/pick\/(?:imports|sync)(?:\?|$)/, async (route) => {
      if (route.request().method() !== "GET") {
        routeError = new Error("Remote catalog write refused");
        await route.abort("blockedbyclient");
      } else await route.continue();
    });
    try {
      await page.goto(
        threadId ? `/workspace/chats/${threadId}` : "/workspace/chats/new",
      );
      for (const step of steps) {
        const stepId = step.step_id ?? "main";
        artifact(
          dir,
          `${stepId}-preflight.json`,
          await preflight(context.request, step),
        );
        if (step.action.branch) {
          const previousUrl = page.url();
          await page
            .getByRole("button", { name: "分叉", exact: true })
            .last()
            .click();
          await expect.poll(() => page.url()).not.toBe(previousUrl);
          await page.waitForURL(/\/workspace\/chats\/(?!new)[^/]+$/);
          threadId = new URL(page.url()).pathname.split("/").at(-1)!;
        }
        if (step.action.open_card_index !== undefined)
          await page
            .getByRole("button", { name: "查看候选", exact: true })
            .nth(step.action.open_card_index)
            .click();
        const reference = step.action.reference_from_step
          ? results.get(step.action.reference_from_step)
          : undefined;
        if (step.action.reference_from_step && !reference)
          throw new Error("Prior step did not produce required result");
        const bound = reference?.id ?? step.action.bound_result_id;
        const selections = (await get(context.request, "/api/pick/selections"))
          .selections;
        const state = manifest.states[step.state_key];
        const attemptId = randomUUID();
        const stateCaptureKey = `${step.state_key}:${attemptId}`;
        if (state.capture_before_step) {
          const chain: Document[] = [];
          let next = bound;
          while (next) {
            if (chain.some((p) => p.id === next))
              throw new Error("Parent chain cycle");
            const result = await get(
              context.request,
              `/api/pick/results/${encodeURIComponent(next)}`,
            );
            if (result.thread_id !== threadId || !parents.has(next))
              throw new Error("Cannot independently establish parent chain");
            const parent = parents.get(next);
            chain.push({
              ...result,
              owner_id: runner.identity.owner_id,
              parent_result_id: parent,
            });
            next = parent;
          }
          captures.states[stateCaptureKey] = {
            owner_id: runner.identity.owner_id,
            captured_at: new Date().toISOString(),
            ...ref(
              "selections_before",
              artifact(dir, `${stepId}-selections-before.json`, selections),
            ),
            ...ref(
              "parent_chain",
              artifact(dir, `${stepId}-parents-before.json`, chain),
            ),
          };
          persist();
        } else {
          expect(selections).toEqual(
            readVerified(base, state, "selections_before"),
          );
          for (const parent of readVerified(
            base,
            state,
            "parent_chain",
          ) as Document[]) {
            const actual = await get(
              context.request,
              `/api/pick/results/${encodeURIComponent(parent.id)}`,
            );
            expect(actual.items).toEqual(parent.items);
            expect(actual.thread_id).toBe(parent.thread_id);
            parents.set(parent.id, parent.parent_result_id);
          }
        }
        const beforeRuns = threadId
          ? await get(context.request, `/api/threads/${threadId}/runs`)
          : [];
        const beforeIds = new Set(
          (beforeRuns as Document[]).map((r) => r.run_id),
        );
        active = {
          step_id: stepId,
          attempt_id: attemptId,
          state_capture_key: state.capture_before_step
            ? stateCaptureKey
            : undefined,
          started_at: new Date().toISOString(),
          expected_bound: bound,
          dispatched: false,
        };
        const started = Date.now();
        if (step.action.kind === "regenerate")
          await page
            .getByRole("button", { name: "重新生成", exact: true })
            .last()
            .click();
        else if (step.action.kind === "edit") {
          await page
            .getByRole("button", { name: "编辑并重新运行", exact: true })
            .last()
            .click();
          await page.getByRole("log").locator("textarea").fill(step.prompt);
          await page
            .getByRole("button", { name: "更新并重新运行", exact: true })
            .click();
        } else {
          await page.locator("textarea").last().fill(step.prompt);
          await page
            .getByRole("button", { name: "Submit", exact: true })
            .click();
        }
        await expect
          .poll(
            () => {
              if (routeError)
                throw new Error("Readiness route guard refused a request");
              return active?.dispatched;
            },
            { timeout: 30000 },
          )
          .toBe(true);
        // Establish the server run before intentionally breaking its browser transport.
        await expect
          .poll(
            async () => {
              const discovered = (
                (await get(
                  context.request,
                  `/api/threads/${threadId}/runs`,
                )) as Document[]
              ).filter((r) => !beforeIds.has(r.run_id));
              if (discovered.length > 1)
                throw new Error("Unexpected concurrent QA run");
              const first = discovered[0];
              if (!first) return false;
              finishAttempt(
                runner.ledger_file,
                active!.attempt_id,
                first.run_id,
                first.status,
              );
              active!.run_id = first.run_id;
              return true;
            },
            { timeout: 30000, intervals: [500, 1000] },
          )
          .toBe(true);
        persist();
        if (step.action.during_run === "reload") await page.reload();
        if (step.action.during_run === "disconnect") {
          await context.setOffline(true);
          await page.waitForTimeout(1000);
          await context.setOffline(false);
          await page.reload();
        }
        if (step.action.during_run === "stop") {
          await expect(
            page
              .getByRole("button", { name: "Submit", exact: true })
              .locator("svg.lucide-square"),
          ).toBeVisible();
          await page
            .getByRole("button", { name: "Submit", exact: true })
            .click();
        }
        let run: Document = {};
        await expect
          .poll(
            async () => {
              const runs = (await get(
                context.request,
                `/api/threads/${threadId}/runs`,
              )) as Document[];
              const current = runs.filter((r) => !beforeIds.has(r.run_id));
              if (current.length > 1)
                throw new Error(
                  "Multiple new runs for a single reserved action",
                );
              run = current[0] ?? {};
              if (run.run_id)
                finishAttempt(
                  runner.ledger_file,
                  active!.attempt_id,
                  run.run_id,
                  run.status,
                );
              return ["success", "error", "timeout", "interrupted"].includes(
                run.status,
              );
            },
            { timeout: runWaitMs, intervals: [1000, 2000] },
          )
          .toBe(true);
        persist();
        const thread = await get(context.request, `/api/threads/${threadId}`);
        const messages: Document[] = (thread.values?.messages ?? []).filter(
          (m: Document) => m.run_id === run.run_id,
        );
        artifact(dir, `${active.attempt_id}-messages.json`, messages);
        const answer = messages
          .filter((m) => m.type === "ai" || m.role === "assistant")
          .map((m) =>
            typeof m.content === "string"
              ? m.content
              : JSON.stringify(m.content),
          )
          .join("\n");
        const calls = extractTools(messages);
        finishAttempt(
          runner.ledger_file,
          active.attempt_id,
          run.run_id,
          run.status,
          {
            ended_at: new Date().toISOString(),
            tool_call_ids: messages.flatMap((m) =>
              (m.tool_calls ?? []).map((c: Document) => c.id),
            ),
          },
        );
        const runResults = (
          await get(context.request, `/api/pick/results?thread_id=${threadId}`)
        ).results.filter((r: Document) => r.run_id === run.run_id);
        for (const result of runResults)
          parents.set(result.id, active.bound_result_id);
        if (runResults.length === 1) results.set(stepId, runResults[0]);
        const actualImports = (await get(context.request, "/api/pick/imports"))
          .batches;
        const source = manifest.sources[step.source_key];
        readVerified(base, source, "rows");
        const boundResults = new Map<string, Document>();
        for (const call of calls) {
          const id = call.response?.result_id;
          if (
            id &&
            ["pick_get_drama_detail", "pick_prepare_selection"].includes(
              call.tool_name,
            )
          )
            boundResults.set(
              id,
              await get(
                context.request,
                `/api/pick/results/${encodeURIComponent(id)}`,
              ),
            );
        }
        const records = calls.map((call) => {
          const normalized = canonical(call);
          const raw = call.response;
          const authoritative = boundResults.get(raw.result_id);
          const batchId =
            raw.catalog_batch_id ?? authoritative?.catalog_batch_id ?? null;
          const actualBatch = actualImports.find(
            (b: Document) => b.id === batchId,
          );
          const knowledgeId =
            raw.documents?.[0]?.batch_id ?? raw.knowledge_batch_id ?? null;
          const knowledgeBatch = actualImports.find(
            (b: Document) => b.id === knowledgeId,
          );
          const isKnowledge = call.tool_name === "pick_search_knowledge";
          const observedSource: Document = isKnowledge
            ? {
                knowledge_batch_id: knowledgeId,
                source_type:
                  knowledgeBatch?.shared &&
                  knowledgeBatch.validation_json?.source === "realshort"
                    ? "realshort_shared"
                    : "unknown",
                shared: knowledgeBatch?.shared ?? null,
                knowledge_sha256:
                  knowledgeId === source.knowledge_batch_id &&
                  knowledgeBatch?.content_hash === source.knowledge_content_hash
                    ? source.knowledge_sha256
                    : null,
              }
            : {
                catalog_batch_id: batchId,
                source_type:
                  actualBatch?.validation_json?.source === "realshort" &&
                  actualBatch.shared
                    ? "realshort_shared"
                    : "unknown",
                shared:
                  raw.data_as_of?.shared ??
                  authoritative?.data_as_of?.shared ??
                  null,
                rows_sha256:
                  actualBatch?.content_hash === source.content_hash &&
                  batchId === source.catalog_batch_id
                    ? source.rows_sha256
                    : null,
                knowledge_batch_id: knowledgeId,
              };
          return {
            case_id: item.case_id,
            step_id: stepId,
            attempt_id: active!.attempt_id,
            state_capture_key: active!.state_capture_key,
            run_id: run.run_id,
            owner_id: runner.identity.owner_id,
            thread_id: threadId,
            started_at: active!.started_at,
            bound_result_id: active!.bound_result_id,
            ...call,
            ...normalized,
            authoritative_result: authoritative ?? null,
            actual_conditions: raw.conditions,
            source: observedSource,
            answer,
            generated_by: manifest.runtime.model,
            terminal_status: run.status,
          };
        });
        captures.records.push(...records);
        const terminal = artifact(dir, `${active.attempt_id}-terminal.json`, {
          run_id: run.run_id,
          status: run.status,
          saved_result_ids: (await selectionsForOwner(context.request)).map(
            (row: Document) => row.source_result_id,
          ),
        });
        captures.run_records ??= [];
        captures.run_records.push({
          case_id: item.case_id,
          step_id: stepId,
          attempt_id: active.attempt_id,
          run_id: run.run_id,
          answer,
          authoritative_terminal: terminal,
        });
        for (const record of records)
          Object.assign(record, ref("authoritative_terminal", terminal));
        artifact(dir, `${active.attempt_id}-run.json`, {
          run,
          elapsed_seconds: (Date.now() - started) / 1000,
          tool_calls: calls.length,
          input_tokens: null,
          output_tokens: null,
          model_calls: null,
          status: "UNVERIFIED",
          sample_conditions: {
            app_sha: manifest.runtime.app_sha,
            model: manifest.runtime.model,
            mode: manifest.runtime.mode,
          },
        });
        persist();
        if (
          records.some(
            (r) =>
              r.source.catalog_batch_id &&
              (r.source.catalog_batch_id !== source.catalog_batch_id ||
                r.source.shared !== true ||
                !r.source.rows_sha256),
          )
        )
          throw new Error(
            "UNVERIFIED_DATA_VERSION: tool returned another source",
          );
        if (
          records.some(
            (r) =>
              r.tool_name === "pick_search_knowledge" &&
              (!r.source.knowledge_sha256 ||
                r.source.knowledge_batch_id !== source.knowledge_batch_id ||
                r.source.shared !== true),
          )
        )
          throw new Error("UNVERIFIED_DATA_VERSION: knowledge source mismatch");
        if (step.save) {
          const prepared = records.find(
            (r) => r.tool_name === "pick_prepare_selection",
          );
          if (!prepared)
            throw new Error("Save requires actual preparation tool response");
          Object.assign(
            prepared,
            ref(
              "selections_after_prepare",
              artifact(
                dir,
                `${stepId}-after-prepare.json`,
                await selectionsForOwner(context.request),
              ),
            ),
          );
          const expected = step.expected;
          const receipts: Document[] = [];
          let tries = 0;
          await page.route("**/api/pick/selections", async (route) => {
            if (route.request().method() !== "POST") return route.continue();
            const body = route.request().postDataJSON();
            expect(body.result_id).toBe(expected.result_id);
            expect(body.item_ids).toEqual(expected.item_ids);
            expect(body.note).toBe(expected.note);
            const command = { ...body, request_id: expected.request_id };
            const committed = await route.fetch({
              postData: JSON.stringify(command),
            });
            if (!committed.ok())
              throw new Error(`Save HTTP ${committed.status()}`);
            const receipt = await committed.json();
            artifact(dir, `${stepId}-raw-receipt-${tries}.json`, receipt);
            receipts.push(receipt);
            artifact(dir, `${stepId}-command-${tries}.json`, command);
            if (tries++ === 0) await route.abort("connectionreset");
            else await route.fulfill({ response: committed });
          });
          await page
            .getByRole("button", { name: /^确认保存（/ })
            .last()
            .click();
          await expect(page.getByRole("alert").last()).toBeVisible();
          await page
            .getByRole("button", { name: /^确认保存（/ })
            .last()
            .click();
          await expect.poll(() => receipts.length).toBe(2);
          const authority = await get(
            context.request,
            `/api/pick/commands/${encodeURIComponent(expected.request_id)}`,
          );
          expect(authority.saved.map((s: Document) => s.id)).toEqual(
            receipts[0]!.saved.map((s: Document) => s.id),
          );
          Object.assign(
            prepared,
            ref("receipts", artifact(dir, `${stepId}-receipts.json`, receipts)),
            ref(
              "selections_after",
              artifact(
                dir,
                `${stepId}-after-save.json`,
                await selectionsForOwner(context.request),
              ),
            ),
          );
          await page.unroute("**/api/pick/selections");
          persist();
        }
        if (step.action.after_reload) await page.reload();
        if (step.action.replay_path) {
          if (!step.action.replay_path.startsWith("/workspace/pick-data?"))
            throw new Error("Invalid replay path");
          await page.goto(step.action.replay_path);
        }
        const assertions = await browserChecks(page, step.browser_assertions);
        if (assertions.length) {
          const screenshot = join(dir, `${stepId}-browser.png`);
          await page.screenshot({ path: screenshot, fullPage: true });
          chmodSync(screenshot, 0o600);
          for (const record of records)
            record.browser_evidence = artifact(
              dir,
              `${stepId}-${encodeURIComponent(record.tool_call_id)}-browser.json`,
              {
                run_id: run.run_id,
                assertions,
                result_id:
                  record.response.id ?? record.response.result_id ?? null,
                source: record.source,
                artifact_refs: [
                  {
                    artifact_file: screenshot,
                    artifact_sha256: sha256(readFileSync(screenshot)),
                  },
                ],
              },
            );
        }
        persist();
        active = null;
        if (routeError)
          throw new Error("Readiness route guard refused a request");
        expect(run.status).toBe(step.expected.terminal_status ?? "success");
      }
    } finally {
      await context.setOffline(false);
      persist();
    }
  });
}
