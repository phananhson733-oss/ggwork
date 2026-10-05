import { createHash } from "node:crypto";
import {
  mkdtempSync,
  writeFileSync,
  chmodSync,
  readFileSync,
  rmSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { afterEach, describe, expect, it } from "@rstest/core";

import {
  budgets,
  redact,
  verifyOwner,
  verifyBatches,
  extractTools,
  reserveCase,
  appendAttempt,
  readVerified,
  loadManifest,
  attributedMessages,
  eventToolInventory,
  sealSaveDispatch,
  ownedSelectionRows,
  resolveExpected,
  toolCallTime,
  frozenResultExport,
  verifyReviewedSequence,
  verifyReferenceIdentities,
  localFixtureTarget,
  type Document,
} from "../../e2e-pick/support/readiness-protocol";

const directories: string[] = [];
function directory() {
  const p = mkdtempSync(join(tmpdir(), "pick-readiness-unit-"));
  directories.push(p);
  return p;
}
afterEach(() => {
  for (const p of directories.splice(0))
    rmSync(p, { recursive: true, force: true });
});

describe("readiness protocol", () => {
  it("derives wait and case budget from verified runtime without shortening product deadlines", () => {
    expect(budgets(600, 3)).toEqual({
      runWaitMs: 660000,
      caseTimeoutMs: 2100000,
    });
    for (const invalid of [0, -1, NaN, 1.2])
      expect(() => budgets(invalid, 1)).toThrow();
  });
  it("refuses wrong identity, admin and missing owner before any run", () => {
    const expected = {
      owner_id: "qa-1",
      email: "qa-azure-readiness@example.test",
    };
    expect(() =>
      verifyOwner(
        { id: "qa-1", email: expected.email, system_role: "user" },
        expected,
      ),
    ).not.toThrow();
    for (const patch of [
      { id: "other" },
      { id: undefined },
      { system_role: "admin" },
      { email: "qa-other@example.test" },
    ])
      expect(() =>
        verifyOwner(
          { id: "qa-1", email: expected.email, system_role: "user", ...patch },
          expected,
        ),
      ).toThrow();
  });
  it("rejects personal imports and incorrect shared source/hash even when count is nonzero", () => {
    const source = {
      catalog_batch_id: "b",
      source_type: "realshort_shared",
      shared: true,
      content_hash: "hash",
    };
    const batch = {
      id: "b",
      kind: "catalog",
      status: "published",
      shared: true,
      content_hash: "hash",
      validation_json: { source: "realshort" },
    };
    expect(() =>
      verifyBatches([batch], { id: "b", shared: true }, source),
    ).not.toThrow();
    expect(() =>
      verifyBatches(
        [{ ...batch, shared: false }],
        { id: "b", shared: false },
        source,
      ),
    ).toThrow();
    expect(() =>
      verifyBatches(
        [{ ...batch, content_hash: "other" }],
        { id: "b", shared: true },
        source,
      ),
    ).toThrow();
    expect(() =>
      verifyBatches(
        [{ ...batch, id: "personal", shared: false }, batch],
        { id: "b", shared: true },
        source,
      ),
    ).toThrow();
  });
  it("checks frozen bytes and refuses missing or changed source exports", () => {
    const p = directory();
    const bytes = '[{"identity":"a"}]';
    writeFileSync(join(p, "rows.json"), bytes, { mode: 0o600 });
    const ref = {
      rows_file: "rows.json",
      rows_sha256: createHash("sha256").update(bytes).digest("hex"),
    };
    expect(readVerified(p, ref, "rows")).toEqual([{ identity: "a" }]);
    writeFileSync(join(p, "rows.json"), "[]");
    expect(() => readVerified(p, ref, "rows")).toThrow();
    chmodSync(join(p, "rows.json"), 0o644);
    expect(() => readVerified(p, ref, "rows")).toThrow();
  });
  it("redacts nested auth values and credential URLs before persistence", () => {
    const safe = JSON.stringify(
      redact(
        {
          authorization: "Bearer hidden",
          nested: { cookie: "hidden", password: "hidden" },
          text: "Bearer abc123 postgresql://u:p@host/db private-password",
        },
        ["private-password"],
      ),
    );
    expect(safe).not.toContain("hidden");
    expect(safe).not.toContain("abc123");
    expect(safe).not.toContain("u:p");
    expect(safe).not.toContain("private-password");
  });
  it("captures every call including errors and unmatched calls; keeps raw arguments independent", () => {
    const messages = [
      {
        type: "ai",
        tool_calls: [
          {
            id: "c1",
            name: "pick_query_candidates",
            args: { filters: { language: "ko" } },
          },
          { id: "c2", name: "pick_count_candidates", args: {} },
        ],
      },
      { type: "tool", tool_call_id: "c1", content: '{"status":"rejected"}' },
    ];
    const calls = extractTools(messages);
    expect(calls).toHaveLength(2);
    expect(calls[0]?.raw_arguments).toEqual({ filters: { language: "ko" } });
    expect(calls[0]?.response).toEqual({ status: "rejected" });
    expect(calls[1]?.capture_status).toBe("MISSING_TOOL_RESPONSE");
  });
  it("reserves whole cases and never hides consumed failed attempts", () => {
    const p = join(directory(), "ledger.json");
    writeFileSync(
      p,
      JSON.stringify({
        max_runs: 40,
        prior_runs: 38,
        reservations: [],
        attempts: [],
      }),
      { mode: 0o600 },
    );
    expect(() => reserveCase(p, "Q14", 3, "manifest")).toThrow();
    const reservation = reserveCase(p, "Q01", 2, "manifest");
    appendAttempt(p, reservation, {
      case_id: "Q01",
      step_id: "a",
      attempt_id: "a1",
      run_id: "r1",
      status: "error",
    });
    appendAttempt(p, reservation, {
      case_id: "Q01",
      step_id: "a",
      attempt_id: "a2",
      run_id: "r2",
      status: "success",
    });
    expect(() =>
      appendAttempt(p, reservation, {
        case_id: "Q01",
        step_id: "a",
        attempt_id: "a3",
        run_id: "r3",
        status: "started",
      }),
    ).toThrow();
    expect(JSON.parse(readFileSync(p, "utf8")).attempts).toHaveLength(2);
    expect(() => reserveCase(p, "Q02", 1, "manifest")).toThrow();
  });
});

describe("readiness configuration isolation", () => {
  it("fails collection without a private manifest and rejects synthetic environment", () => {
    expect(() => loadManifest(undefined, {})).toThrow();
    const path = join(directory(), "manifest.json");
    writeFileSync(
      path,
      JSON.stringify({
        spec_version: "pick-readiness-v1.1",
        environment: "synthetic-local",
      }),
      { mode: 0o600 },
    );
    expect(() => loadManifest(path, {})).toThrow("remote-qa");
  });
  it("isolates test collection and disables traces which include authentication", () => {
    const config = readFileSync("playwright.pick.readiness.config.ts", "utf8");
    const synthetic = readFileSync("playwright.pick.config.ts", "utf8");
    expect(config).toContain('testMatch: "readiness.spec.ts"');
    expect(config).toContain('trace: "off"');
    expect(synthetic).toContain(
      'testMatch: ["personal-selection.spec.ts", "pick-data-board.spec.ts"]',
    );
  });
  it("refuses insecure remote URLs, missing opt-in and missing runtime evidence", () => {
    const path = join(directory(), "manifest.json");
    const manifest = {
      spec_version: "pick-readiness-v1.1",
      environment: "remote-qa",
      locked_at: "2026-10-05T00:00:00Z",
      review: { reviewer: "human", basis: "reviewed" },
      runner: {
        target_url: "https://qa.example.test",
        identity: { owner_id: "qa", email: "qa-azure-test@example.test" },
        evidence_dir: "/private/evidence",
        ledger_file: "/private/ledger.json",
      },
      runtime: {},
    };
    writeFileSync(path, JSON.stringify(manifest), { mode: 0o600 });
    const env = {
      PICK_E2E_REMOTE_QA: "1",
      PICK_E2E_URL: "https://qa.example.test",
      PICK_E2E_EMAIL: "qa-azure-test@example.test",
      PICK_E2E_PASSWORD: "synthetic-password",
    };
    expect(() =>
      loadManifest(path, { ...env, PICK_E2E_URL: "http://qa.example.test" }),
    ).toThrow();
    expect(() =>
      loadManifest(path, { ...env, PICK_E2E_REMOTE_QA: "0" }),
    ).toThrow();
    expect(() => loadManifest(path, env)).toThrow("artifact");
  });
});

it("loads a fully locked protocol fixture and detects modified runtime evidence", () => {
  const root = directory();
  const put = (name: string, value: unknown) => {
    const raw = JSON.stringify(value);
    writeFileSync(join(root, name), raw, { mode: 0o600 });
    return { file: name, hash: createHash("sha256").update(raw).digest("hex") };
  };
  const target = "https://qa.example.invalid";
  const runtime = {
    app_sha: "a".repeat(40),
    model: "synthetic-config-only",
    mode: "thinking",
    run_timeout_seconds: 600,
    request_timeout_seconds: 300,
    stream_chunk_timeout_seconds: 300,
  };
  const config = put("runtime.json", {
    ...runtime,
    verified_at: "2026-10-05T00:00:00Z",
    target_url: target,
    max_output_tokens: 32000,
    effort: "high",
    request_context: { model_name: null, thinking_enabled: true },
  });
  const rows = put("rows.json", []);
  const metadata = put("metadata.json", {});
  const empty = put("empty.json", []);
  const conditions = {
    theater: null,
    language: "en",
    channel: null,
    query: null,
    tags: [],
    limit: 5,
    exclude_selected: true,
    confirmed_eligible_only: true,
    exclude_previous: false,
    signal_kind: null,
    sort: "evidence_date",
    exclude_posted: false,
    posted_account: null,
    hot_only: false,
  };
  const manifest: Document = {
    spec_version: "pick-readiness-v1.1",
    environment: "remote-qa",
    locked_at: "2026-10-05T00:00:00Z",
    review: {
      reviewer: "synthetic-fixture-reviewer",
      basis: "configuration unit test only",
    },
    runtime: {
      ...runtime,
      config_evidence_file: config.file,
      config_evidence_sha256: config.hash,
    },
    runner: {
      target_url: target,
      runtime_context: { model_name: null, thinking_enabled: true },
      identity: { owner_id: "qa", email: "qa-azure-test@example.invalid" },
      evidence_dir: root,
      ledger_file: join(root, "ledger.json"),
    },
    sources: {
      s: {
        source_type: "realshort_shared",
        shared: true,
        catalog_batch_id: "config-only",
        content_hash: "dummy",
        rows_file: rows.file,
        rows_sha256: rows.hash,
        metadata_file: metadata.file,
        metadata_sha256: metadata.hash,
      },
    },
    states: {
      before: {
        owner_id: "qa",
        selections_before_file: empty.file,
        selections_before_sha256: empty.hash,
        parent_chain_file: empty.file,
        parent_chain_sha256: empty.hash,
      },
    },
    cases: [
      {
        case_id: "Q01",
        case_type: "query",
        planned_max_runs: 1,
        source_key: "s",
        state_key: "before",
        prompt: "Config-only synthetic prompt",
        action: { kind: "prompt" },
        expected: {
          allowed_actions: ["pick_query_candidates"],
          allowed_condition_sets: [conditions],
          expected_outcome: "query_success",
          semantic_rubric: ["Independent review required"],
          browser_assertions: [],
        },
      },
    ],
  };
  const path = join(root, "manifest.json");
  writeFileSync(path, JSON.stringify(manifest), { mode: 0o600 });
  const env = {
    PICK_E2E_REMOTE_QA: "1",
    PICK_E2E_URL: target,
    PICK_E2E_EMAIL: manifest.runner.identity.email,
    PICK_E2E_PASSWORD: "synthetic",
  };
  expect(loadManifest(path, env).manifest.runtime.run_timeout_seconds).toBe(
    600,
  );
  const original = manifest.cases[0];
  const compound = {
    ...original,
    tool_contracts: [
      {
        tool_name: "pick_count_candidates",
        case_type: "count",
        min_occurrences: 1,
        expected: {
          ...original.expected,
          allowed_actions: ["pick_count_candidates"],
          expected_outcome: "count_success",
        },
      },
      {
        tool_name: "pick_query_candidates",
        case_type: "query",
        min_occurrences: 1,
        expected: original.expected,
      },
    ],
  };
  delete compound.expected;
  delete compound.case_type;
  manifest.cases = [compound];
  writeFileSync(path, JSON.stringify(manifest));
  expect(loadManifest(path, env).manifest.cases[0].tool_contracts).toHaveLength(
    2,
  );
  manifest.cases = [
    {
      ...compound,
      tool_contracts: [],
      terminal_contract: {
        case_type: "recovery",
        expected: {
          terminal_status: "interrupted",
          saved_result_ids: [],
          semantic_rubric: ["No save claim"],
          browser_assertions: [],
        },
      },
    },
  ];
  writeFileSync(path, JSON.stringify(manifest));
  expect(
    loadManifest(path, env).manifest.cases[0].terminal_contract.case_type,
  ).toBe("recovery");
  put("runtime.json", { ...runtime, run_timeout_seconds: 120 });
  expect(() => loadManifest(path, env)).toThrow("hash");
});

it("retries within the original whole-case reservation, without exceeding planned runs", () => {
  const path = join(directory(), "ledger.json");
  writeFileSync(
    path,
    JSON.stringify({
      max_runs: 40,
      prior_runs: 0,
      reservations: [],
      attempts: [],
    }),
    { mode: 0o600 },
  );
  const first = reserveCase(path, "Q01", 2, "hash", 1);
  appendAttempt(path, first, {
    case_id: "Q01",
    step_id: "main",
    attempt_id: "one",
    run_id: "run-one",
    status: "error",
  });
  expect(reserveCase(path, "Q01", 2, "hash", 1)).toBe(first);
  expect(() => reserveCase(path, "Q01", 2, "hash", 1)).toThrow("retry");
});

it("uses host history attribution while auditing independent run event envelopes", () => {
  const ai = {
    type: "ai",
    id: "ai1",
    content: "",
    tool_calls: [
      {
        id: "call1",
        name: "pick_query_candidates",
        args: { filters: { language: "en" } },
      },
    ],
    additional_kwargs: {},
  };
  const tool = {
    type: "tool",
    id: "tool1",
    tool_call_id: "call1",
    content: '{"id":"result1"}',
    additional_kwargs: {},
  };
  // This is the actual host AIMessage/ToolMessage serialized shape: no top-level run_id.
  expect([ai, tool].filter((m: Document) => m.run_id === "run1")).toHaveLength(
    0,
  );
  const events = [
    {
      seq: 1,
      run_id: "run1",
      event_type: "llm.ai.response",
      category: "message",
      content: ai,
    },
    {
      seq: 2,
      run_id: "run1",
      event_type: "llm.tool.result",
      category: "message",
      content: tool,
    },
    {
      seq: 3,
      run_id: "other",
      event_type: "llm.ai.response",
      content: { ...ai, tool_calls: [{ id: "wrong" }] },
    },
  ];
  const history = [
    {
      values: {
        messages: [
          { ...ai, run_id: "run1" },
          { ...tool, run_id: "run1" },
        ],
      },
    },
  ];
  expect(
    extractTools(attributedMessages(history, events, "run1")),
  ).toHaveLength(1);
  expect(eventToolInventory(events, "run1")).toEqual(["call1"]);
  // Missing checkpoint attribution must not erase an independently observed event call.
  expect(
    extractTools(
      attributedMessages(
        [{ values: { messages: [ai, tool] } }],
        events,
        "run1",
      ),
    ),
  ).toHaveLength(1);
});

it("seals the actual UI request id and rejects a regenerated retry id", () => {
  const expected = {
    result_id: "r",
    item_ids: ["i"],
    note: "review",
    request_id: { capture_before_dispatch: true },
  };
  const first = {
    request_id: "ui-generated",
    result_id: "r",
    item_ids: ["i"],
    note: "review",
  };
  const sealed = sealSaveDispatch(expected, first, undefined);
  expect(sealed.request_id).toBe("ui-generated");
  expect(sealSaveDispatch(expected, { ...first }, sealed)).toEqual(first);
  expect(() =>
    sealSaveDispatch(expected, { ...first, request_id: "new-ui-id" }, sealed),
  ).toThrow();
});

it("refuses remote synthetic fixture targets even with opt-in", () => {
  expect(localFixtureTarget("http://localhost:3008")).toBe(
    "http://localhost:3008",
  );
  expect(() => localFixtureTarget("https://qa.example.invalid")).toThrow();
});

it("uses identical verified-owner shape before prepare and after save, including existing rows", () => {
  const rows = [
    {
      id: "existing",
      identity: "drama",
      note: "old",
      snapshot_json: { title: "Synthetic" },
      state: "selected",
      version: 1,
    },
  ];
  expect(ownedSelectionRows(rows, "qa")).toEqual([
    { ...rows[0], owner_id: "qa" },
  ]);
  expect(ownedSelectionRows(ownedSelectionRows(rows, "qa"), "qa")).toEqual(
    ownedSelectionRows(rows, "qa"),
  );
  expect(() =>
    ownedSelectionRows([{ ...rows[0], owner_id: "another" }], "qa"),
  ).toThrow();
});

it("resolves compound typed bindings from a prelocked query occurrence, never detail arguments", () => {
  const records = [
    { tool_name: "pick_count_candidates", response: { total: 2 } },
    {
      tool_name: "pick_query_candidates",
      response: { id: "first", items: [{ item_id: "a" }, { item_id: "b" }] },
    },
    {
      tool_name: "pick_query_candidates",
      response: { id: "second", items: [{ item_id: "wrong" }] },
    },
    {
      tool_name: "pick_get_drama_detail",
      raw_arguments: { result_id: "second", item_id: "wrong" },
    },
  ];
  const expected = {
    result_id: { from_tool: "pick_query_candidates", occurrence: 1 },
    item_id: { position: 2 },
  };
  expect(resolveExpected(expected, records)).toEqual({
    result_id: "first",
    item_id: "b",
  });
  expect(expected.result_id).toEqual({
    from_tool: "pick_query_candidates",
    occurrence: 1,
  });
});

it("preserves actual per-call timestamps from scoped persisted envelopes, including JSON content", () => {
  const events = [
    {
      run_id: "r",
      event_type: "llm.ai.response",
      created_at: "2026-10-05T00:00:01.000001+00:00",
      content: JSON.stringify({ tool_calls: [{ id: "q" }] }),
    },
    {
      run_id: "other",
      event_type: "llm.ai.response",
      created_at: "2020-01-01T00:00:00Z",
      content: { tool_calls: [{ id: "d" }] },
    },
    {
      run_id: "r",
      event_type: "llm.ai.response",
      created_at: "2026-10-05T00:00:02.000003+00:00",
      content: { tool_calls: [{ id: "d" }] },
    },
  ];
  expect(toolCallTime(events, "r", "q")).toBe(
    "2026-10-05T00:00:01.000001+00:00",
  );
  expect(toolCallTime(events, "r", "d")).toBe(
    "2026-10-05T00:00:02.000003+00:00",
  );
  expect(toolCallTime(events, "r", "missing")).toBeNull();
});

it("exports the full authoritative API evidence without applying model projection", () => {
  const evidence = [
    {
      citation_id: "i:1",
      kind: "sm",
      source_ref: "synthetic:full",
      observed_at: null,
      rank: null,
      grade: "S",
      note: "uncertain",
      value: 0,
    },
  ];
  const result = {
    id: "r",
    catalog_batch_id: "b",
    created_at: "2026-10-05T00:00:01Z",
    items: [{ item_id: "i", identity: "x", evidence }],
    data_as_of: { shared: true },
    rule_version: "pick-rules-v1",
    ranking_version: "evidence-date-v1",
  };
  const frozen = frozenResultExport(
    result,
    [
      {
        id: "b",
        shared: true,
        content_hash: "hash",
        validation_json: { source: "realshort" },
      },
    ],
    { catalog_batch_id: "b", content_hash: "hash", rows_sha256: "rows-hash" },
    "qa",
  );
  expect(frozen.items[0].evidence).toEqual(evidence);
  expect(frozen.items[0].evidence[0].source_ref).toBe("synthetic:full");
  expect(frozen.owner_id).toBe("qa");
  expect(frozen.source.rows_sha256).toBe("rows-hash");
  expect(result).not.toHaveProperty("owner_id");
});

it("preserves independently reviewed exact action sequences and prerequisite identities", () => {
  const allowed = [
    ["pick_query_candidates"],
    ["pick_count_candidates", "pick_query_candidates"],
  ];
  expect(() =>
    verifyReviewedSequence(allowed, [
      "pick_count_candidates",
      "pick_query_candidates",
    ]),
  ).not.toThrow();
  expect(() =>
    verifyReviewedSequence(allowed, [
      "pick_query_candidates",
      "pick_query_candidates",
    ]),
  ).toThrow();
  expect(() =>
    verifyReferenceIdentities(
      { items: [{ identity: "a" }, { identity: "b" }] },
      ["a", "b"],
    ),
  ).not.toThrow();
  expect(() =>
    verifyReferenceIdentities({ items: [{ identity: "wrong" }] }, ["a"]),
  ).toThrow();
});

it("refuses a prospective offline manifest before credential/runtime evaluation", () => {
  const path = join(directory(), "prospective.json");
  writeFileSync(
    path,
    JSON.stringify({
      spec_version: "pick-readiness-v1.1",
      environment: "remote-qa",
      prospective: true,
      locked_at: "2026-10-05T00:00:00Z",
      review: { reviewer: "independent", basis: "offline conversion" },
    }),
    { mode: 0o600 },
  );
  expect(() => loadManifest(path, {})).toThrow("Prospective");
});
