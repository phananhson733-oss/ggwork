# Independent readiness checker contract (synthetic fixtures)

Every committed JSON file here is **synthetic**. Browser, semantic and performance
records exercise the file contract only; they are not live model or browser QA.
The five catalog identities have a hand-calculated answer: excluding the saved
`selected` and parent chain `parent → grandparent` leaves `a,b`, in that order.
Latest `kd` rank order is `b,a`; only `a` has confirmed YouTube permission.

Run from the repository root:

```sh
python scripts/pick-readiness-verify.py \
  --expectations customizations/pick-workbench/tests/fixtures/readiness/expectations.json \
  --captures customizations/pick-workbench/tests/fixtures/readiness/captures.json \
  --out /tmp/pick-readiness-report.json
```

The checker reads local UTF-8 JSON only. SHA-256 always covers **raw file bytes**,
not reserialized JSON. Each `*_file` resolves relative to the JSON manifest that
owns the reference. Never put real catalog data, raw answers, owner/run identifiers,
credentials or private source URLs in this fixture directory.

## Locked expectations

`spec_version=pick-readiness-v1.1`, `environment`, `locked_at` (timezone required),
`review:{reviewer,basis}`, `runtime`, `sources`, `states`, and nonempty `cases` are
required. Runtime fields and the independently hashed `config_evidence` file must
agree on app SHA, model, mode and all three timeout values. Remote environment
`remote-qa` requires `source_type=realshort_shared, shared=true`.

Sources contain `catalog_batch_id, source_type, shared`, separate `rows_file` /
`rows_sha256` and `metadata_file` / `metadata_sha256`. Rows are full frozen catalog
rows, including identity, title, theater, language, tags, availability, signals,
channel_rules and posted. Metadata requires `source_as_of, published_at,
freshness, rule_version, ranking_version, frozen_data_as_of`. Source dates must
be ISO dates/timestamps; freshness preserves the real nonempty object with
`catalogImportedAt,reelshortSyncedAt` timestamp values. `frozen_data_as_of` is
the actual five-field `{source_as_of,published_at,freshness,scope,shared}` object
(with optional positive `mirror_version`), agrees with independent metadata, and
is compared exactly against returned query/count/detail data_as_of. Unknown
mandatory metadata remains UNVERIFIED. Query rule/ranking versions are also
compared, never ignored. Knowledge sources additionally carry
`knowledge_batch_id, knowledge_file, knowledge_sha256`.

Static states contain `owner_id, captured_at`, separately hashed
`selections_before` and `parent_chain` references. Empty arrays are valid; missing
files are not. Selection rows contain identity. Parent chain is immediate parent
first, ends with null `parent_result_id`, and each entry contains `id, owner_id,
thread_id, parent_result_id, items`; derived query/count parents also contain
`conditions,catalog_batch_id`. Detail/save items contain `item_id,identity` plus
frozen facts used by that case. The bound parent also requires its original
`catalog_batch_id,source,data_as_of,rule_version,ranking_version`; parent.source
is `{catalog_batch_id,source_type,shared,rows_sha256}` independently captured
for that frozen batch. All of these must match the step's declared source,
so a foreign historical batch cannot be disguised by correct response metadata. An `excluded_json` field is never trusted.

For a state produced between steps, pre-lock this placeholder in expectations:

```json
{"owner_id":"synthetic-owner","capture_before_step":{"case_id":"Q14","step_id":"second"}}
```

Capture the actual state as `captures.states[state_key]`, with the same full state
shape and hashes, **before** submitting that step. Its file references resolve
relative to captures. The expectations bytes remain unchanged.
For retries, each record uses `state_capture_key="<state_key>:<attempt_id>"`
and `captures.states[state_capture_key]` holds that attempt's immutable state.
The plain state_key fallback is permitted only for a single attempt. State capture time
must not exceed the authoritative run ledger `started_at`.

Every case has `case_id, planned_max_runs` and either a single implicit `main`
step, or `steps`. Every step has `step_id` (explicit in a steps array), `case_type`,
`source_key,state_key,expected`. Single steps can keep these fields on the case.
`prompt` documents the pre-reviewed intent. Each expected object requires
`allowed_actions,expected_outcome,semantic_rubric`; optional `required_arguments`
checks exact raw tool arguments; `terminal_status` defaults to `success`.

Query/count expected conditions must be complete. The 14 fields are:
`theater,language,channel,query,tags,limit,exclude_selected,
confirmed_eligible_only,exclude_previous,signal_kind,sort,exclude_posted,
posted_account,hot_only`. Defaults are explicit in expectations.json. Unknown fields fail closed. Actual omissions use
these reviewed defaults. A query/count model call uses `raw_arguments.filters`
and optional `use_latest`; a host-bound parent is `record.bound_result_id`, never
a fabricated model argument. Derived raw filters are merged over captured parent
conditions independently before comparing with expected and effective conditions.
Query/count `response.conditions` is required and normalized independently as
well; it must agree with the prelocked allowed set, raw-derived conditions and
actual effective conditions.

## Captures and complete attempts

Captures contains `spec_version,expectations_sha256,started_at,attempts,records`
and a separately hashed `run_ledger_file/run_ledger_sha256`. The ledger is an
independent complete run inventory. Its entries exactly equal inline attempts:
`{case_id,step_id,attempt_id,run_id,status,started_at,ended_at,tool_call_ids}`.
The tool_call_ids inventory must equal the complete per-attempt record IDs,
including error calls. Authority status must match every record terminal status
and the expected terminal; an observed failed attempt remains FAIL. All records
fall within the ledger start/end interval. Ledger start must follow both
expectations locking and captures start. Preserve failed attempts and every
tool call; do not replace an attempt with its successful retry. A missing attempt
capture remains unverified. The offline checker cannot discover an omitted event
if both the ledger and captures omit it; completeness depends on the independent
export. Global distinct run count is capped at 40 and case count at planned budget.

Each record requires the ledger identifiers plus `owner_id,thread_id,tool_call_id,
tool_name,started_at,raw_arguments,outcome,source,response,answer,generated_by,
terminal_status`. Source contains actual `catalog_batch_id,source_type,shared,
rows_sha256`; knowledge also has `knowledge_batch_id`. A data version mismatch
is `UNVERIFIED_DATA_VERSION`, never a same-version regression or PASS.

Response schemas (canonical capture shapes; preserve original raw tool payloads
privately as well):

| case_type | expected additions | response and additional captures |
|---|---|---|
| query | full `allowed_condition_sets` | `id,items[{identity}],matched_total`; zero results require `zero_diagnosis` with independently checked single relaxations |
| count | full `allowed_condition_sets` | `total,by_theater,by_language`; zero diagnosis for zero count |
| detail | `result_id,item_id,fact_fields` | exact raw result_id/item_id binding, `identity`, all named fields equal frozen parent item |
| prepare_save | `result_id,item_ids,request_id,note` | `requires_confirmation,result_id,item_ids,note`; hashed `selections_after_prepare`, `selections_after`, `receipts` refs; at least two identical actual `{request_id,saved:[{id,identity,status,version}]}` receipts |
| clarification | `missing_parameters,allowed_branches` | `missing_parameters,branch` |
| refusal | `reason_codes` | `status=refused,reason_code` |
| recovery | `terminal_status,saved_result_ids` | `status,saved_result_ids`; hashed `authoritative_terminal` containing run_id/status/saved_result_ids |
| knowledge | `citation_ids` | `citations` exactly equal independent entries, each batch_id bound to knowledge_batch_id; no invented data_as_of |

Detail and save always validate the frozen parent owner/thread and chain,
regardless of exclude_previous. Save raw arguments use result_id/item_ids or
host bound_result_id plus positions; positions resolve against frozen item order.
Omitted choice arguments require independently captured state.selected_item_ids.
The expected item set/note is checked independently of the tool response.
Every requested item_id must exist in the frozen result; unknown IDs cannot be
silently filtered out. Duplicate known IDs follow the product's deduplication
and frozen ordering behavior.

For save cases, before/after-prepare/after exports include **all** relevant
selected and removed rows (not only the active list), with `id,owner_id,identity,
state,version,source_result_id,source_item_id,note,snapshot_json`. Receipt IDs map
to exact after rows. Created rows are version 1; existing selected rows preserve
all previous facts; restored rows keep their ID, increment version and take the
requested source/item/note/snapshot. Unrelated rows must remain unchanged.

All query matched_total, count total/group counts and zero-diagnosis counts
are strict nonnegative integers; booleans and numerically equal floats are
invalid. Zero-diagnosis null counts retain their explicit unavailable meaning.

An actual observed error remains FAIL even if its typed response is incomplete.
This oracle verifies filtering, exclusion, order, counts, selected detail facts,
knowledge citations and save state/receipt invariants. Full UI rendering and prose
facts are separate evidence layers; it does not claim to understand natural language.

## Five evidence layers and exit codes

`intent` and `data_state` are computed. The other layers need evidence:

- `browser_evidence:{evidence_file,evidence_sha256}` references
  `{run_id,result_id,source,assertions:[{name,passed}],artifact_refs:[{artifact_file,artifact_sha256}]}`.
  Assertion names exactly equal prelocked expected.browser_assertions; result_id
  (null where not applicable) and source bind the record. Every artifact must
  exist locally and match its byte hash. The E2E producer owns
  assertion/trace truth; the checker verifies the bound evidence, not replaying it.
- `performance_evidence` uses the same reference shape; payload requires `run_id,
  input_tokens,output_tokens,elapsed_seconds,tool_calls,model_calls,sample_conditions`.
  Metrics must be finite nonnegative numbers; counts are integers and booleans
  are invalid. Metrics have explicit `scope:"run"`; tool_calls must equal the
  distinct complete tool_call_ids in the independent ledger for that run,
  across any steps. A known inventory contradiction is FAIL. Null or literal "unknown" metrics remain unverified, never zero
  or claimed savings. sample_conditions must be nonempty text. PASS means metrics
  were supplied, not that a statistically significant improvement was demonstrated.
- `semantic_review:{reviewer,answer_sha256,judgments}` binds exact answer UTF-8 bytes.
  Reviewer must differ from `generated_by`; every locked rubric needs one judgment
  in the same order with `rubric,status,reason,fact_refs`. Independent humans or
  review agents author these judgments. No regex or structural result creates a
  semantic PASS.

Missing external evidence yields NOT_RUN; invalid/incomplete material yields
UNVERIFIED. Reports retain every attempt and all five layer statuses. Exit codes:
0 only all requested checks pass; 1 any observed FAIL; 2 invalid/incomplete/no cases
without an observed FAIL. There is no network, login, collection or model invocation.

## User-action steps, multiple tools and terminal-only records

A step remains one user interaction. For a run that may call different tools,
pre-lock `tool_contracts` on that step instead of pretending every response has
one type:

```json
{
  "tool_contracts": [
    {"tool_name":"pick_count_candidates","case_type":"count","min_occurrences":1,"expected":{}},
    {"tool_name":"pick_query_candidates","case_type":"query","min_occurrences":1,"expected":{}}
  ]
}
```

The empty expected objects above are schematic: each must contain the complete
ordinary typed expectation, including allowed_actions=[that exact tool_name],
outcome, all conditions, browser assertions and semantic rubric. Names are
unique. Array order is the allowed order within each attempt. Minimum occurrences
are checked across the complete action step. Unexpected calls, wrong ordering or
missing required calls cannot pass. The actual tool_name selects the prelocked
contract; actual arguments never select an easier type. Ledger tool_call_ids is
an ordered complete inventory, not a set that can be reordered after execution.

A step can additionally declare `terminal_contract:{case_type,expected}`.
The case_type is clarification or recovery. It requires exactly one actual
terminal record per attempt. Single terminal-only clarification/recovery steps
can keep the contract directly on the step. A terminal record has
`record_kind:"terminal"`, normal case/step/attempt/run/owner/thread/timestamp
identity, `terminal_status,answer,generated_by`, external evidence references and
`authoritative_terminal_file/sha256`. It must **not** contain tool_call_id,
tool_name or raw_arguments. Its independent authority file is:

```json
{"run_id":"synthetic-run","status":"success","answer_sha256":"<actual-answer-byte-hash>","saved_result_ids":[]}
```

Terminal expected.allowed_actions is `["terminal"]`, expected_outcome is
`"terminal"`. Recovery checks expected.saved_result_ids against authority.
Clarification locks `missing_parameters` and `clarification_rubric` (an exact
member of semantic_rubric). The independent semantic reviewer judges that text
criterion against the actual answer and locked missing parameters. The producer
never manufactures missing_parameters or branch facts from expectations. Missing
review remains NOT_RUN/UNVERIFIED. Terminal browser evidence has null result_id
and source when no result/source exists. Performance scope remains the actual
run, with tool_calls=0 only when the independent inventory is empty.

For an intentionally interrupted attempt followed by success, pre-lock e.g.
`attempt_terminal_statuses:["cancelled","success"]`. The complete independent
ledger's per-step ordinal determines the expected status. Each attempt requires
a terminal capture; omitting one or the final planned attempt cannot pass.
Without this explicit plan, a failed/cancelled attempt is still FAIL even when
a later retry succeeds.

### New result references within a compound run

Generated result IDs can be locked symbolically rather than fabricated in
advance: `expected.result_id={"from_tool":"pick_query_candidates","occurrence":1}`
and `expected.item_id={"position":2}`. For prepare, item_ids may be
`{"positions":[1,3]}`. The referenced producer must be a prelocked query tool;
the checker resolves only that prior same-run occurrence and frozen positions,
never searches for an output that happens to match actual arguments. Known
historical result/item IDs continue to use literal strings.

To read a newly created result absent from the pre-run state, detail/prepare can
supply `bound_result_file/sha256` referring to
`{captured_at,result:<full independent immutable-result export>}`. Its result has
the usual parent identity/source/frozen metadata/items plus created_at. The
export may be read after the run; its actual capture timestamp is preserved.
The result must have existed before the consuming tool, match exactly one prior
same-run query output's result ID, item IDs/order/identities and conditions, and
agree with independent catalog facts. It does not overwrite the pre-run state.

### Real UI-generated save request IDs

The expected request_id may be the predeclared placeholder
`{"capture_before_dispatch":true}`. All other save intent is prelocked (literal
or the producer/position reference above). The first actual UI request is sealed
before dispatch in `save_dispatch_file/sha256`:

```json
{"captured_at":"2026-10-05T00:00:02Z","request":{"request_id":"actual-generated-id","result_id":"p","item_ids":["p1"],"note":"synthetic note"}}
```

`save_requests_file/sha256` contains the actual first and retry dispatches as
`[{dispatched_at,request},...]`. All complete raw request objects must equal the
sealed first request, timestamps must be ordered, and every real receipt must
refer to that first actual ID. Do not intercept/rewrite the application ID to
match a test constant. The first sealed payload must also match the other
locked intent fields. Literal request IDs remain supported for deterministic
synthetic cases.
