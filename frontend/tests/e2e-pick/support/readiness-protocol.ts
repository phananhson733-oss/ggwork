/** Private readiness artifacts only. This module never imports product selection logic. */
import { createHash, randomUUID } from "node:crypto";
import {
  chmodSync,
  closeSync,
  mkdirSync,
  openSync,
  readFileSync,
  renameSync,
  statSync,
  unlinkSync,
  writeFileSync,
} from "node:fs";
import { dirname, isAbsolute, resolve } from "node:path";

// Captures deliberately retain unknown provider/tool fields without interpreting them as expectations.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export type Document = Record<string, any>;
function requireValue(value: unknown, message: string): asserts value {
  if (!value) throw new Error(message);
}
export function sha256(value: string | Buffer) {
  return createHash("sha256").update(value).digest("hex");
}
export function budgets(seconds: number, runs: number) {
  requireValue(
    Number.isSafeInteger(seconds) &&
      seconds > 0 &&
      Number.isSafeInteger(runs) &&
      runs > 0 &&
      runs <= 40,
    "Invalid verified run budget",
  );
  const runWaitMs = (seconds + 60) * 1000;
  return { runWaitMs, caseTimeoutMs: runs * runWaitMs + 120000 };
}
export function readPrivate(path: string) {
  requireValue(
    (statSync(path).mode & 0o077) === 0,
    "Private artifact must have mode 0600",
  );
  return readFileSync(path);
}
export function readVerified(
  base: string,
  ref: Document,
  field: string,
): Document | Document[] {
  requireValue(
    typeof ref[`${field}_file`] === "string" &&
      /^[a-f0-9]{64}$/.test(ref[`${field}_sha256`] ?? ""),
    "Missing frozen artifact reference",
  );
  const bytes = readPrivate(resolve(base, ref[`${field}_file`]));
  requireValue(
    sha256(bytes) === ref[`${field}_sha256`],
    "Frozen artifact hash mismatch",
  );
  return JSON.parse(bytes.toString()) as Document | Document[];
}
export function redact(value: unknown, secrets: string[] = []): unknown {
  if (typeof value === "string") {
    let text = value
      .replace(
        /\b(?:cookie|set-cookie|authorization|proxy-authorization|x-csrf-token):[^\r\n]*/gi,
        "[REDACTED_HEADER]",
      )
      .replace(/\bBearer\s+[\w.+\/-]+/gi, "Bearer [REDACTED]")
      .replace(
        /\b(?:postgres(?:ql)?(?:\+\w+)?|mysql|redis|https?):\/\/[^\s"<>]*@[^\s"<>]*/gi,
        "[REDACTED_URL]",
      );
    for (const secret of secrets.filter(Boolean))
      text = text.split(secret).join("[REDACTED]");
    return text;
  }
  if (Array.isArray(value)) return value.map((item) => redact(item, secrets));
  if (value && typeof value === "object")
    return Object.fromEntries(
      Object.entries(value).map(([key, item]) => [
        key,
        /^(authorization|proxy-authorization|cookie|set-cookie|password|access_token|refresh_token|auth_token|api_key|apikey|client_secret|secret|connection_string|database_url|csrf_token)$/i.test(
          key,
        )
          ? "[REDACTED]"
          : redact(item, secrets),
      ]),
    );
  return value;
}
export function writePrivate(
  path: string,
  value: unknown,
  secrets: string[] = [],
) {
  mkdirSync(dirname(path), { recursive: true, mode: 0o700 });
  chmodSync(dirname(path), 0o700);
  const temp = `${path}.${randomUUID()}.tmp`;
  writeFileSync(temp, JSON.stringify(redact(value, secrets), null, 2), {
    mode: 0o600,
  });
  renameSync(temp, path);
  chmodSync(path, 0o600);
  return { evidence_file: path, evidence_sha256: sha256(readFileSync(path)) };
}
export function verifyOwner(user: Document, expected: Document) {
  requireValue(
    user.id &&
      user.id === expected.owner_id &&
      user.email === expected.email &&
      user.email.startsWith("qa-azure-") &&
      user.system_role === "user",
    "Readiness requires the locked ordinary QA identity",
  );
}
export function verifyBatches(
  batches: Document[],
  current: Document,
  source: Document,
) {
  requireValue(
    source.source_type === "realshort_shared" && source.shared === true,
    "Readiness source must be shared RealShort",
  );
  requireValue(
    !batches.some(
      (b) =>
        ["catalog", "knowledge"].includes(b.kind) &&
        b.status === "published" &&
        b.shared !== true,
    ),
    "Personal imports contaminate readiness identity",
  );
  requireValue(
    current?.id === source.catalog_batch_id && current.shared === true,
    "UNVERIFIED_DATA_VERSION: current catalog differs",
  );
  for (const [kind, id, hash] of [
    ["catalog", source.catalog_batch_id, source.content_hash],
    ["knowledge", source.knowledge_batch_id, source.knowledge_content_hash],
  ]) {
    if (!id && kind === "knowledge") continue;
    const batch = batches.find((b) => b.id === id && b.kind === kind);
    requireValue(
      batch?.status === "published" &&
        batch.shared === true &&
        batch.validation_json?.source === "realshort" &&
        hash &&
        batch.content_hash === hash,
      "UNVERIFIED_DATA_VERSION: source provenance/hash differs",
    );
    if (kind === "knowledge")
      requireValue(
        batches.find((b) => b.kind === kind && b.status === "published")?.id ===
          id,
        "UNVERIFIED_DATA_VERSION: current knowledge differs",
      );
  }
}
export function extractTools(messages: Document[]) {
  const responses = new Map(
    messages
      .filter((m) => m.type === "tool" || m.role === "tool")
      .map((m) => [m.tool_call_id, m]),
  );
  return messages.flatMap((m) =>
    (m.tool_calls ?? []).map((call: Document) => {
      const raw = responses.get(call.id)?.content;
      let response: unknown = null;
      try {
        response = typeof raw === "string" ? JSON.parse(raw) : raw;
      } catch {
        response = { unparsed_tool_output: raw };
      }
      return {
        tool_call_id: call.id,
        tool_name: call.name,
        raw_arguments: call.args,
        raw_response: raw ?? null,
        response: response ?? {},
        capture_status:
          raw === undefined ? "MISSING_TOOL_RESPONSE" : "CAPTURED",
      };
    }),
  );
}
function updateLedger<T>(path: string, work: (ledger: Document) => T): T {
  const lock = `${path}.lock`;
  const fd = openSync(lock, "wx", 0o600);
  try {
    const ledger = JSON.parse(readPrivate(path).toString()) as Document;
    const result = work(ledger);
    writePrivate(path, ledger);
    return result;
  } finally {
    closeSync(fd);
    unlinkSync(lock);
  }
}
export function reserveCase(
  path: string,
  caseId: string,
  maxRuns: number,
  manifestHash: string,
  minimumRuns: number = maxRuns,
) {
  return updateLedger(path, (ledger) => {
    requireValue(
      Number.isSafeInteger(ledger.max_runs) &&
        ledger.max_runs > 0 &&
        ledger.max_runs <= 40 &&
        Number.isSafeInteger(ledger.prior_runs) &&
        ledger.prior_runs >= 0 &&
        Array.isArray(ledger.reservations) &&
        Array.isArray(ledger.attempts),
      "Invalid global ledger",
    );
    requireValue(
      Number.isSafeInteger(maxRuns) &&
        maxRuns > 0 &&
        Number.isSafeInteger(minimumRuns) &&
        minimumRuns > 0 &&
        minimumRuns <= maxRuns,
      "Invalid case reservation",
    );
    const previous = ledger.reservations.find(
      (r: Document) =>
        r.case_id === caseId && r.manifest_sha256 === manifestHash,
    );
    if (previous) {
      const used = ledger.attempts.filter(
        (a: Document) => a.reservation_id === previous.id,
      ).length;
      requireValue(!previous.retry_used, "Case retry already used");
      requireValue(
        previous.max_runs === maxRuns && maxRuns - used >= minimumRuns,
        "Insufficient remaining planned case runs for retry",
      );
      previous.retry_used = true;
      return previous.id as string;
    }
    const reserved = ledger.reservations.reduce(
      (n: number, r: Document) => n + r.max_runs,
      0,
    );
    requireValue(
      Number.isSafeInteger(maxRuns) &&
        maxRuns > 0 &&
        ledger.prior_runs + reserved + maxRuns <= ledger.max_runs,
      "Insufficient global run budget for whole case",
    );
    requireValue(
      ledger.reservations.filter(
        (r: Document) =>
          r.case_id === caseId && r.manifest_sha256 === manifestHash,
      ).length < 2,
      "Case already retried once",
    );
    const id = randomUUID();
    ledger.reservations.push({
      id,
      case_id: caseId,
      max_runs: maxRuns,
      manifest_sha256: manifestHash,
      reserved_at: new Date().toISOString(),
    });
    return id;
  });
}
export function appendAttempt(
  path: string,
  reservation: string,
  attempt: Document,
) {
  updateLedger(path, (ledger) => {
    const entry = ledger.reservations.find(
      (r: Document) => r.id === reservation,
    );
    requireValue(
      entry && entry.case_id === attempt.case_id,
      "Missing case reservation",
    );
    const used = ledger.attempts.filter(
      (a: Document) => a.reservation_id === reservation,
    );
    requireValue(
      used.length < entry.max_runs,
      "Reserved case run budget exhausted",
    );
    requireValue(
      !ledger.attempts.some(
        (a: Document) => a.attempt_id === attempt.attempt_id,
      ),
      "Duplicate attempt",
    );
    ledger.attempts.push({ ...attempt, reservation_id: reservation });
  });
}
export function finishAttempt(
  path: string,
  attemptId: string,
  runId: string,
  status: string,
  evidence: Document = {},
) {
  updateLedger(path, (ledger) => {
    const attempt = ledger.attempts.find(
      (a: Document) => a.attempt_id === attemptId,
    );
    requireValue(attempt, "Attempt was not reserved before request");
    attempt.run_id = runId;
    attempt.status = status;
    Object.assign(attempt, evidence);
  });
}
export function loadManifest(
  path: string | undefined,
  env: Record<string, string | undefined> = process.env,
) {
  requireValue(
    path && isAbsolute(path),
    "PICK_READINESS_MANIFEST must name a private absolute path",
  );
  const bytes = readPrivate(path);
  const manifest = JSON.parse(bytes.toString()) as Document;
  requireValue(
    manifest.spec_version === "pick-readiness-v1.1" &&
      manifest.environment === "remote-qa" &&
      manifest.synthetic !== true,
    "Readiness only accepts remote-qa manifests",
  );
  requireValue(
    manifest.review?.reviewer && manifest.review?.basis && manifest.locked_at,
    "Expectations require prior independent review",
  );
  requireValue(
    manifest.prospective !== true,
    "Prospective manifest is not approved for execution",
  );
  const runner = manifest.runner;
  const target = new URL(env.PICK_E2E_URL ?? "");
  requireValue(
    env.PICK_E2E_REMOTE_QA === "1" &&
      target.protocol === "https:" &&
      !["localhost", "127.0.0.1", "[::1]"].includes(target.hostname) &&
      !target.username &&
      !target.password &&
      target.href === new URL(runner?.target_url ?? "").href,
    "Remote QA target/opt-in mismatch",
  );
  requireValue(
    env.PICK_E2E_EMAIL &&
      env.PICK_E2E_PASSWORD &&
      env.PICK_E2E_EMAIL === runner.identity?.email &&
      runner.identity?.owner_id &&
      runner.identity.email.startsWith("qa-azure-"),
    "Private ordinary QA credentials and locked owner required",
  );
  requireValue(
    isAbsolute(runner.evidence_dir) && isAbsolute(runner.ledger_file),
    "Private evidence directory and global ledger paths required",
  );
  const base = dirname(path);
  const config = readVerified(
    base,
    manifest.runtime,
    "config_evidence",
  ) as Document;
  for (const key of [
    "app_sha",
    "model",
    "mode",
    "run_timeout_seconds",
    "request_timeout_seconds",
    "stream_chunk_timeout_seconds",
  ])
    requireValue(
      config[key] === manifest.runtime[key],
      "Verified runtime configuration mismatch",
    );
  requireValue(
    config.verified_at &&
      config.target_url === runner.target_url &&
      config.max_output_tokens &&
      config.effort &&
      /^[a-f0-9]{40}$/.test(config.app_sha),
    "Actual runtime snapshot is incomplete",
  );
  requireValue(
    config.request_context &&
      runner.runtime_context &&
      typeof runner.runtime_context.thinking_enabled === "boolean" &&
      Object.prototype.hasOwnProperty.call(
        runner.runtime_context,
        "model_name",
      ) &&
      JSON.stringify(config.request_context) ===
        JSON.stringify(runner.runtime_context),
    "Verified request model context required",
  );
  requireValue(
    Array.isArray(manifest.cases) && manifest.cases.length > 0,
    "No readiness cases",
  );
  const seen = new Set();
  for (const item of manifest.cases) {
    requireValue(
      /^Q(?:0[1-9]|1[0-9]|20)$/.test(item.case_id) && !seen.has(item.case_id),
      "Invalid/duplicate readiness case ID",
    );
    seen.add(item.case_id);
    budgets(manifest.runtime.run_timeout_seconds, item.planned_max_runs);
    const steps = item.steps ?? [item];
    requireValue(
      steps.length > 0 && steps.length <= item.planned_max_runs,
      "Case steps exceed planned budget",
    );
    for (const step of steps) {
      requireValue(
        step.action &&
          ["prompt", "regenerate", "edit"].includes(step.action.kind),
        "Each step needs a concrete action",
      );
      if (step.action.kind !== "regenerate")
        requireValue(
          typeof step.prompt === "string" && step.prompt.length,
          "Prompt/edit step requires fixed text",
        );
      requireValue(
        /^[a-zA-Z0-9_-]+$/.test(step.step_id ?? "main"),
        "Invalid step ID",
      );
      const contracts: Document[] =
        step.tool_contracts ?? (step.expected ? [step] : []);
      const allContracts = [
        ...contracts,
        ...(step.terminal_contract ? [step.terminal_contract] : []),
      ];
      requireValue(allContracts.length, "Step has no prelocked typed contract");
      const names = new Set<string>();
      const browserNames = (step.browser_assertions ?? []).map(
        (check: Document) => check.name,
      );
      for (const contract of allContracts) {
        requireValue(
          [
            "query",
            "count",
            "detail",
            "prepare_save",
            "clarification",
            "refusal",
            "recovery",
            "knowledge",
          ].includes(contract.case_type),
          "Invalid case type",
        );
        const expected = contract.expected;
        requireValue(
          expected?.semantic_rubric?.length &&
            Array.isArray(expected.browser_assertions) &&
            expected.browser_assertions.every((name: string) =>
              browserNames.includes(name),
            ),
          "Independent semantic/browser contracts required",
        );
        if (contract === step.terminal_contract)
          requireValue(
            ["clarification", "recovery"].includes(contract.case_type),
            "Invalid terminal contract",
          );
        else
          requireValue(
            expected.allowed_actions?.length && expected.expected_outcome,
            "Tool action/outcome contract required",
          );
        if (step.tool_contracts && contract !== step.terminal_contract) {
          requireValue(
            typeof contract.tool_name === "string" &&
              !names.has(contract.tool_name) &&
              Number.isSafeInteger(contract.min_occurrences) &&
              contract.min_occurrences >= 0,
            "Invalid ordered tool contract",
          );
          names.add(contract.tool_name);
        }
        if (["query", "count"].includes(contract.case_type)) {
          const keys = [
            "theater",
            "language",
            "channel",
            "query",
            "tags",
            "limit",
            "exclude_selected",
            "confirmed_eligible_only",
            "exclude_previous",
            "signal_kind",
            "sort",
            "exclude_posted",
            "posted_account",
            "hot_only",
          ].sort();
          requireValue(
            Array.isArray(expected.allowed_condition_sets) &&
              expected.allowed_condition_sets.length &&
              expected.allowed_condition_sets.every(
                (conditions: Document) =>
                  JSON.stringify(Object.keys(conditions).sort()) ===
                  JSON.stringify(keys),
              ),
            "Expected conditions must explicitly contain all 14 reviewed fields",
          );
        }
      }
      const source = manifest.sources?.[step.source_key];
      requireValue(
        source?.source_type === "realshort_shared" &&
          source.shared === true &&
          source.content_hash,
        "Missing shared source provenance",
      );
      readVerified(base, source, "rows");
      readVerified(base, source, "metadata");
      if (source.knowledge_batch_id) readVerified(base, source, "knowledge");
      const state = manifest.states?.[step.state_key];
      requireValue(
        state?.owner_id === runner.identity.owner_id,
        "Missing owned pre-state",
      );
      if (!state.capture_before_step) {
        readVerified(base, state, "selections_before");
        readVerified(base, state, "parent_chain");
      } else
        requireValue(
          state.capture_before_step.case_id === item.case_id &&
            state.capture_before_step.step_id === (step.step_id ?? "main"),
          "Dynamic state must bind its exact step",
        );
    }
  }
  return { manifest, hash: sha256(bytes), path, base };
}

/** /history annotates checkpoint messages; /events independently scopes persisted envelopes. */
export function attributedMessages(
  history: Document[],
  events: Document[],
  runId: string,
): Document[] {
  const messages: Document[] = (history[0]?.values?.messages ?? []).filter(
    (m: Document) => m.run_id === runId,
  );
  const identity = (m: Document) =>
    m.type === "tool" ? `tool:${m.tool_call_id}` : `message:${m.id}`;
  const seen = new Set(messages.map(identity));
  for (const event of events) {
    if (
      event.run_id !== runId ||
      event.category !== "message" ||
      !["llm.ai.response", "llm.tool.result", "llm.human.input"].includes(
        event.event_type,
      )
    )
      continue;
    const message =
      typeof event.content === "string"
        ? JSON.parse(event.content)
        : event.content;
    if (!message || typeof message !== "object") continue;
    if (!seen.has(identity(message))) {
      messages.push({ ...message, run_id: runId });
      seen.add(identity(message));
    }
  }
  return messages;
}
export function eventToolInventory(
  events: Document[],
  runId: string,
): string[] {
  const ids = new Set<string>();
  for (const event of events) {
    if (event.run_id !== runId || event.category !== "message") continue;
    const content =
      typeof event.content === "string"
        ? JSON.parse(event.content)
        : event.content;
    if (event.event_type === "llm.ai.response")
      for (const call of content?.tool_calls ?? []) {
        requireValue(
          typeof call.id === "string" && call.id,
          "Event tool call missing ID",
        );
        ids.add(call.id);
      }
    if (
      event.event_type === "llm.tool.result" &&
      typeof content?.tool_call_id === "string"
    )
      ids.add(content.tool_call_id);
  }
  return [...ids];
}
export function sealSaveDispatch(
  expected: Document,
  request: Document,
  first: Document | undefined,
): Document {
  requireValue(
    typeof request.request_id === "string" && request.request_id.length > 0,
    "UI save request ID missing",
  );
  requireValue(
    request.result_id === expected.result_id &&
      JSON.stringify(request.item_ids) === JSON.stringify(expected.item_ids) &&
      request.note === expected.note,
    "UI save intent mismatch",
  );
  if (typeof expected.request_id === "string")
    requireValue(
      request.request_id === expected.request_id,
      "UI save request ID mismatch",
    );
  else
    requireValue(
      expected.request_id?.capture_before_dispatch === true,
      "Dynamic request ID placeholder was not prelocked",
    );
  if (first)
    requireValue(
      JSON.stringify(request) === JSON.stringify(first),
      "Raw retry request changed",
    );
  return { ...request };
}
export function localFixtureTarget(url: string): string {
  const parsed = new URL(url);
  requireValue(
    ["localhost", "127.0.0.1", "[::1]"].includes(parsed.hostname),
    "Synthetic fixtures require an isolated localhost target",
  );
  return url;
}

export function ownedSelectionRows(
  rows: Document[],
  ownerId: string,
): Document[] {
  return rows.map((row) => {
    requireValue(
      row.owner_id === undefined || row.owner_id === ownerId,
      "Selection owner mismatch",
    );
    return { ...row, owner_id: ownerId };
  });
}
export function resolveExpected(
  expected: Document,
  prior: Document[],
): Document {
  if (typeof expected.result_id !== "object" || !expected.result_id)
    return { ...expected };
  const binding = expected.result_id;
  requireValue(
    binding.from_tool === "pick_query_candidates" &&
      Number.isSafeInteger(binding.occurrence) &&
      binding.occurrence > 0,
    "Invalid prelocked producer binding",
  );
  const result = prior.filter(
    (record) => record.tool_name === binding.from_tool,
  )[binding.occurrence - 1]?.response;
  requireValue(
    result?.id && Array.isArray(result.items),
    "Bound query occurrence unavailable",
  );
  const resolved: Document = { ...expected, result_id: result.id };
  const item = (position: number) => {
    requireValue(
      Number.isSafeInteger(position) &&
        position > 0 &&
        result.items[position - 1]?.item_id,
      "Bound position unavailable",
    );
    return result.items[position - 1].item_id;
  };
  if (typeof expected.item_id === "object" && expected.item_id)
    resolved.item_id = item(expected.item_id.position);
  if (expected.item_ids?.positions)
    resolved.item_ids = expected.item_ids.positions.map(item);
  return resolved;
}

export function toolCallTime(
  events: Document[],
  runId: string,
  callId: string,
): string | null {
  const observed = events.filter((event) => {
    if (event.run_id !== runId || event.event_type !== "llm.ai.response")
      return false;
    const content =
      typeof event.content === "string"
        ? JSON.parse(event.content)
        : event.content;
    return (content?.tool_calls ?? []).some(
      (call: Document) => call.id === callId,
    );
  });
  const times = [...new Set(observed.map((event) => event.created_at))];
  requireValue(times.length <= 1, "Ambiguous persisted tool-call timestamp");
  return typeof times[0] === "string" ? times[0] : null;
}
export function frozenResultExport(
  result: Document,
  imports: Document[],
  source: Document,
  ownerId: string,
): Document {
  const batch = imports.find((entry) => entry.id === result.catalog_batch_id);
  return {
    ...result,
    owner_id: ownerId,
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

export function verifyReviewedSequence(
  allowed: string[][] | undefined,
  actual: string[],
): void {
  if (allowed)
    requireValue(
      allowed.some(
        (sequence) => JSON.stringify(sequence) === JSON.stringify(actual),
      ),
      "Actual tool sequence violates independently reviewed intent",
    );
}
export function verifyReferenceIdentities(
  result: Document,
  identities: string[],
): void {
  requireValue(
    JSON.stringify(result.items?.map((item: Document) => item.identity)) ===
      JSON.stringify(identities),
    "Prerequisite result differs from independently reviewed identities",
  );
}
