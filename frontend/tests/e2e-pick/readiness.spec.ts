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
  attributedMessages,
  eventToolInventory,
  sealSaveDispatch,
  ownedSelectionRows,
  resolveExpected,
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
async function runEvents(
  request: APIRequestContext,
  threadId: string,
  runId: string,
): Promise<Document[]> {
  const events: Document[] = [];
  let cursor = 0;
  for (;;) {
    const batch = (await get(
      request,
      `/api/threads/${threadId}/runs/${runId}/events?limit=2000${cursor ? `&after_seq=${cursor}` : ""}`,
    )) as Document[];
    if (!Array.isArray(batch))
      throw new Error("Invalid event inventory response");
    if (!batch.length) return events;
    const next = Math.max(...batch.map((event) => event.seq));
    if (!Number.isSafeInteger(next) || next <= cursor)
      throw new Error("Event inventory cursor did not advance");
    events.push(...batch);
    cursor = next;
    if (batch.length < 2000) return events;
  }
}
function observedResult(
  result: Document,
  imports: Document[],
  source: Document,
): Document {
  const batch = imports.find((entry) => entry.id === result.catalog_batch_id);
  return {
    ...result,
    owner_id: runner.identity.owner_id,
    source: {
      catalog_batch_id: result.catalog_batch_id,
      source_type:
        batch?.shared && batch.validation_json?.source === "realshort"
          ? "realshort_shared"
          : "unknown",
      shared: result.data_as_of?.shared ?? null,
      rows_sha256:
        batch?.content_hash === source.content_hash &&
        result.catalog_batch_id === source.catalog_batch_id
          ? source.rows_sha256
          : null,
    },
  };
}
function toolTime(events: Document[], callId: string): string | null {
  return (
    events.find(
      (event) =>
        event.event_type === "llm.ai.response" &&
        (event.content?.tool_calls ?? []).some(
          (call: Document) => call.id === callId,
        ),
    )?.created_at ?? null
  );
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
  return ownedSelectionRows(response.selections, runner.identity.owner_id);
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
function terminalContractFor(
  step: Document | undefined | null,
): Document | undefined {
  return (
    step?.terminal_contract ??
    (step && ["clarification", "recovery"].includes(step.case_type)
      ? step
      : undefined)
  );
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
    let currentStep: Document | null = null;
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
        currentStep = step;
        const stepId = step.step_id ?? "main";
        const proof = await preflight(context.request, step);
        artifact(dir, `${stepId}-preflight.json`, proof);
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
        const selections = await selectionsForOwner(context.request);
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
              ...observedResult(
                result,
                proof.imports.batches,
                manifest.sources[step.source_key],
              ),
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
          before_run_ids: [...beforeIds],
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
        const csrf = (await context.cookies()).find(
          (cookie) => cookie.name === "csrf_token",
        )?.value;
        const historyResponse = await context.request.post(
          `/api/threads/${threadId}/history`,
          { data: { limit: 1 }, headers: csrf ? { "X-CSRF-Token": csrf } : {} },
        );
        if (!historyResponse.ok())
          throw new Error(
            `History attribution HTTP ${historyResponse.status()}`,
          );
        const history: Document[] = await historyResponse.json();
        const events = await runEvents(context.request, threadId, run.run_id);
        const messages = attributedMessages(history, events, run.run_id);
        artifact(dir, `${active.attempt_id}-history.json`, history);
        artifact(dir, `${active.attempt_id}-events.json`, events);
        artifact(dir, `${active.attempt_id}-messages.json`, messages);
        const answer = messages
          .filter((m) => m.type === "ai" || m.role === "assistant")
          .map((m) =>
            typeof m.content === "string"
              ? m.content
              : JSON.stringify(m.content),
          )
          .join("\n");
        const inventory = eventToolInventory(events, run.run_id);
        const calls = extractTools(messages).sort(
          (a, b) =>
            inventory.indexOf(a.tool_call_id) -
            inventory.indexOf(b.tool_call_id),
        );
        finishAttempt(
          runner.ledger_file,
          active.attempt_id,
          run.run_id,
          run.status,
          {
            ended_at: new Date().toISOString(),
            tool_call_ids: eventToolInventory(events, run.run_id),
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
              observedResult(
                await get(
                  context.request,
                  `/api/pick/results/${encodeURIComponent(id)}`,
                ),
                actualImports,
                source,
              ),
            );
        }
        const records: Document[] = calls.map((call) => {
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
            started_at: toolTime(events, call.tool_call_id),
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
        for (const record of records)
          if (record.authoritative_result)
            Object.assign(
              record,
              ref(
                "bound_result",
                artifact(
                  dir,
                  `${active.attempt_id}-${encodeURIComponent(record.tool_call_id)}-bound-result.json`,
                  {
                    captured_at: new Date().toISOString(),
                    result: record.authoritative_result,
                  },
                ),
              ),
            );
        captures.records.push(...records);
        const terminal = artifact(dir, `${active.attempt_id}-terminal.json`, {
          run_id: run.run_id,
          status: run.status,
          answer_sha256: sha256(answer),
          saved_result_ids: (await selectionsForOwner(context.request)).map(
            (row: Document) => row.source_result_id,
          ),
        });
        if (terminalContractFor(step)) {
          const terminalRecord: Document = {
            record_kind: "terminal",
            case_id: item.case_id,
            step_id: stepId,
            attempt_id: active.attempt_id,
            run_id: run.run_id,
            owner_id: runner.identity.owner_id,
            thread_id: threadId,
            started_at: active.started_at,
            state_capture_key: active.state_capture_key,
            terminal_status: run.status,
            answer,
            generated_by: manifest.runtime.model,
            ...ref("authoritative_terminal", terminal),
          };
          records.push(terminalRecord);
          captures.records.push(terminalRecord);
        }
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
              r.source?.catalog_batch_id &&
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
          const expected = resolveExpected(
            step.tool_contracts?.find(
              (contract: Document) =>
                contract.tool_name === "pick_prepare_selection",
            )?.expected ?? step.expected,
            records.slice(0, records.indexOf(prepared)),
          );
          const receipts: Document[] = [];
          let tries = 0;
          let sealed: Document | undefined;
          let originalBody: string | null = null;
          const requests: Document[] = [];
          const saveAttempts: Document[] = [];
          await page.route("**/api/pick/selections", async (route) => {
            if (route.request().method() !== "POST") return route.continue();
            const body = route.request().postDataJSON();
            const rawBody = route.request().postData();
            const observedSave: Document = {
              observed_at: new Date().toISOString(),
              request: body,
              raw_body: rawBody,
              status: "received",
            };
            saveAttempts.push(observedSave);
            artifact(dir, `${stepId}-save-attempts.json`, saveAttempts);
            try {
              const command = sealSaveDispatch(expected, body, sealed);
              if (!sealed) {
                sealed = command;
                originalBody = rawBody;
                Object.assign(
                  prepared,
                  ref(
                    "save_dispatch",
                    artifact(dir, `${stepId}-save-dispatch.json`, {
                      captured_at: new Date().toISOString(),
                      request: command,
                    }),
                  ),
                );
                persist();
              } else if (rawBody !== originalBody)
                throw new Error("Literal UI retry body changed");
              requests.push({
                dispatched_at: new Date().toISOString(),
                request: command,
              });
              Object.assign(
                prepared,
                ref(
                  "save_requests",
                  artifact(dir, `${stepId}-save-requests.json`, requests),
                ),
              );
              persist();
              // The actual UI request, including its original request_id, is never rewritten.
              const committed = await route.fetch();
              if (!committed.ok())
                throw new Error(`Save HTTP ${committed.status()}`);
              const receipt = await committed.json();
              receipts.push(receipt);
              artifact(dir, `${stepId}-raw-receipt-${tries}.json`, receipt);
              Object.assign(
                prepared,
                ref(
                  "receipts",
                  artifact(dir, `${stepId}-receipts.json`, receipts),
                ),
              );
              persist();
              observedSave.status = "committed";
              artifact(dir, `${stepId}-save-attempts.json`, saveAttempts);
              if (tries++ === 0) await route.abort("connectionreset");
              else await route.fulfill({ response: committed });
            } catch (saveError) {
              observedSave.status = "rejected_or_unverified";
              observedSave.error =
                saveError instanceof Error
                  ? saveError.message
                  : "Save observation failed";
              artifact(dir, `${stepId}-save-attempts.json`, saveAttempts);
              routeError =
                saveError instanceof Error
                  ? saveError
                  : new Error("Save request guard failed");
              await route.abort("blockedbyclient");
            }
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
            `/api/pick/commands/${encodeURIComponent(sealed!.request_id)}`,
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
              `${stepId}-${encodeURIComponent(record.tool_call_id ?? "terminal")}-browser.json`,
              {
                run_id: run.run_id,
                assertions: assertions.filter((assertion) =>
                  (
                    (record.record_kind === "terminal"
                      ? terminalContractFor(step)
                      : step.tool_contracts?.find(
                          (contract: Document) =>
                            contract.tool_name === record.tool_name,
                        )
                    )?.expected ?? step.expected
                  )?.browser_assertions?.includes(assertion.name),
                ),
                result_id:
                  record.response?.id ?? record.response?.result_id ?? null,
                source: record.source ?? null,
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
        expect(run.status).toBe(
          terminalContractFor(step)?.expected?.terminal_status ??
            step.expected?.terminal_status ??
            "success",
        );
      }
    } catch (error) {
      await context.setOffline(false);
      captures.failures ??= [];
      const failure: Document = {
        case_id: item.case_id,
        step_id: active?.step_id ?? currentStep?.step_id ?? "main",
        attempt_id: active?.attempt_id ?? null,
        captured_at: new Date().toISOString(),
        error_type: error instanceof Error ? error.name : "UnknownError",
        error_message: error instanceof Error ? error.message : String(error),
      };
      captures.failures.push(failure);
      try {
        const screenshot = join(
          dir,
          `failed-${active?.attempt_id ?? randomUUID()}.png`,
        );
        await page.screenshot({
          path: screenshot,
          fullPage: true,
          timeout: 10000,
        });
        chmodSync(screenshot, 0o600);
        failure.screenshot = {
          artifact_file: screenshot,
          artifact_sha256: sha256(readFileSync(screenshot)),
        };
      } catch {
        failure.screenshot_status = "UNVERIFIED";
      }
      if (active?.dispatched && threadId && currentStep) {
        try {
          const runs = (await get(
            context.request,
            `/api/threads/${threadId}/runs`,
          )) as Document[];
          const candidates = active.run_id
            ? runs.filter((run) => run.run_id === active!.run_id)
            : runs.filter(
                (run) => !active!.before_run_ids.includes(run.run_id),
              );
          if (candidates.length !== 1)
            throw new Error("Unable to identify a single submitted run");
          const observed = candidates[0]!;
          const events = await runEvents(
            context.request,
            threadId,
            observed.run_id,
          );
          const csrf = (await context.cookies()).find(
            (cookie) => cookie.name === "csrf_token",
          )?.value;
          const historyResponse = await context.request.post(
            `/api/threads/${threadId}/history`,
            {
              data: { limit: 1 },
              headers: csrf ? { "X-CSRF-Token": csrf } : {},
            },
          );
          const history = historyResponse.ok()
            ? await historyResponse.json()
            : [];
          const messages = attributedMessages(history, events, observed.run_id);
          const answer = messages
            .filter((message) => message.type === "ai")
            .map((message) =>
              typeof message.content === "string"
                ? message.content
                : JSON.stringify(message.content),
            )
            .join("\n");
          const terminal = [
            "success",
            "error",
            "timeout",
            "interrupted",
          ].includes(observed.status);
          const status = terminal ? observed.status : "unknown";
          artifact(dir, `${active.attempt_id}-failure-events.json`, events);
          artifact(dir, `${active.attempt_id}-failure-history.json`, history);
          finishAttempt(
            runner.ledger_file,
            active.attempt_id,
            observed.run_id,
            status,
            {
              ended_at: terminal ? new Date().toISOString() : null,
              tool_call_ids: eventToolInventory(events, observed.run_id),
              observed_status: observed.status,
            },
          );
          const authority = artifact(
            dir,
            `${active.attempt_id}-failure-terminal.json`,
            {
              run_id: observed.run_id,
              status,
              observed_status: observed.status,
              answer_sha256: sha256(answer),
              saved_result_ids: (await selectionsForOwner(context.request)).map(
                (row: Document) => row.source_result_id,
              ),
            },
          );
          const inventory = eventToolInventory(events, observed.run_id);
          for (const call of extractTools(messages).sort(
            (a, b) =>
              inventory.indexOf(a.tool_call_id) -
              inventory.indexOf(b.tool_call_id),
          )) {
            if (
              captures.records.some(
                (record: Document) =>
                  record.run_id === observed.run_id &&
                  record.tool_call_id === call.tool_call_id,
              )
            )
              continue;
            captures.records.push({
              record_kind: "tool",
              case_id: item.case_id,
              step_id: active.step_id,
              attempt_id: active.attempt_id,
              run_id: observed.run_id,
              owner_id: runner.identity.owner_id,
              thread_id: threadId,
              started_at: toolTime(events, call.tool_call_id),
              state_capture_key: active.state_capture_key,
              bound_result_id: active.bound_result_id,
              ...call,
              ...canonical(call),
              actual_conditions: call.response?.conditions,
              source: {
                catalog_batch_id: call.response?.catalog_batch_id ?? null,
                shared: call.response?.data_as_of?.shared ?? null,
                source_type: "unknown",
                rows_sha256: null,
              },
              answer,
              generated_by: manifest.runtime.model,
              terminal_status: status,
              ...ref("authoritative_terminal", authority),
              capture_status: "PARTIAL_AFTER_FAILURE",
            });
          }
          if (
            terminalContractFor(currentStep) &&
            !captures.records.some(
              (record: Document) =>
                record.record_kind === "terminal" &&
                record.run_id === observed.run_id,
            )
          )
            captures.records.push({
              record_kind: "terminal",
              case_id: item.case_id,
              step_id: active.step_id,
              attempt_id: active.attempt_id,
              run_id: observed.run_id,
              owner_id: runner.identity.owner_id,
              thread_id: threadId,
              started_at: active.started_at,
              state_capture_key: active.state_capture_key,
              terminal_status: status,
              answer,
              generated_by: manifest.runtime.model,
              ...ref("authoritative_terminal", authority),
            });
          failure.reconciliation_status = terminal
            ? "OBSERVED_TERMINAL"
            : "UNVERIFIED_RUNNING";
        } catch (reconciliationError) {
          failure.reconciliation_status = "UNVERIFIED";
          failure.reconciliation_error =
            reconciliationError instanceof Error
              ? reconciliationError.message
              : "Unknown reconciliation error";
        }
      }
      persist();
      throw error;
    } finally {
      await context.setOffline(false);
      persist();
    }
  });
}
