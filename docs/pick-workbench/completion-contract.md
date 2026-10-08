# Pick completion v1 — frozen integration contract (T1)

This contract is additive. Python DTOs: `customizations/pick-workbench/ggwork_pick/completion_contracts.py`; frontend runtime parsers and inferred types: `frontend/src/core/pick/completion-types.ts`. Full round-trip wire examples: `frontend/tests/unit/core/pick/fixtures/completion-v1.json`. Existing `PickConditions`, stored result/item JSON, `types.ts`, `/results/{id}/notes`, and historical fixtures are unchanged. Query/plan/publication metadata must not be added to those objects.

T1 defines contracts and acceptance fixtures only. Routes, owner checks, SQL, host publication, plan persistence, export checks and external feedback activation remain downstream work. Private migration head inspected at base `bb070f1f`: **0008 → 0007**. T7 must inspect head again and allocate the next private migration; no host migration or managed extension snapshot changes belong here.

## Routes and wire shapes

All paths below are relative to the authenticated Gateway; the frontend uses its existing credential/CSRF fetcher. DTO name means the exact closed shape in both files above, including inherited fields and defaults. Frontend closed parsers trim editable strings like StrictInput; backend remains authoritative for PostgreSQL storable-text validation, owner checks, calendar/local-time/timezone resolution and body limits. Mirror source models preserve raw source text. GET queries are listed explicitly. No request accepts `owner_id`; all records resolve the authenticated principal first. An unauthorized record reads as 404 regardless of its actual existence.

| Method / path | Request | Response |
| --- | --- | --- |
| POST `/api/pick/query` | `CommonQuery` | 200 `QueryResponse` |
| GET `/api/pick/plans?offset=0&limit=20` | offset nonnegative integer; limit 1–100 | 200 `{items: Plan[], total: integer, next_offset: integer|null}` |
| POST `/api/pick/plans` | `PlanCreate` | 200 `Plan` |
| GET `/api/pick/plans/{id}` | stable plan ID | 200 `Plan` |
| PATCH `/api/pick/plans/{id}` | `PlanUpdate`; complete editable row list | 200 `Plan` |
| POST `/api/pick/plans/{id}/preview` | `PlanVersionCommand` | 200 `PlanPreview` including blocked rows |
| POST `/api/pick/plans/{id}/exports` | `PlanExportCommand` | 200 `PlanExport` immutable receipt |
| GET `/api/pick/exports/{id}` | stable export ID | immutable CSV bytes, `text/csv; charset=utf-8`, attachment filename from receipt |
| GET `/api/pick/feedback/posts` | `ReviewQuery` fields as query parameters | 200 `ReviewPosts` |
| GET `/api/pick/feedback/plan-links?plan_id=...` | owner-scoped plan ID | 200 `{items: PlanLink[]}` |
| POST `/api/pick/feedback/plan-links` | `PlanLinkCommand` | 200 `PlanLink` |

Business errors use `{detail: CompletionError}`. Code/status mapping: invalid_query=422; unauthorized=401; not_found=404; version_gone=410; period_missing=422; source_unavailable=503; query_timeout=504; version_conflict/export_blocked/link_conflict=409. `retryable` is true for temporary source/timeout failures, false for invalid input or required revision/confirmation changes. `current_version` is owner-safe and only meaningful for a revision conflict. Existing validation 422 and authentication envelopes remain supported; do not replace global host error handling. Errors never return success-shaped empty rows or zero counts. Only safe human messages reach the UI; no database exception, raw source body or credentials.

## Common query

`domain`: candidates/catalog/rankings/posted/rules. `scope`: candidate_pool/full_catalog. These are independent and must be displayed. `pin=null` resolves a single current version; response always returns the resolved `QueryPin` (`catalog_batch_id`, paired `mirror_version`, `knowledge_batch_id`, `rule_version`, optional private `feedback_version_id`). An explicit historical pin either resolves exactly or fails `version_gone`; no silent current substitution. A run cannot mix conflicting pins. Owner/version/permission context belongs in cache keys.

Legacy candidate adapters must explicitly preserve the current `PickConditions` defaults (especially exclude_selected=true), tags, confirmed_eligible_only, exclude_previous and hot_only. CommonQuery defaults are board-neutral, not a replacement for candidate defaults. The existing observation-specific conditions stay on the existing observation query path until its own explicit common DTO mapping and parity tests; do not drop those conditions during cutover.

Input filters are explicit: query (title/Chinese title fuzzy and stable source ID exact), source/source_id, language/theater/channel/account, published_from/to, exclude_posted/selected, signal_kind; board filters posted_filter (`no/yes/pool`), posted_state (`pub/sched/none/nomatch`), with_off, signal_only, wide, youtube_ok, dated_only, in_use_only; rank (all 13 theater bases and 8 ReelShort ranks), grade, rs_sort/locale/bucket and legacy_week_label. `result_id` is a replay reference and must pass existing owner/thread checks. A request does not infer country from language. `language=null` means no filter, `language=""` explicitly means unknown/empty source language; the board URL's `__unknown__` maps to this empty value.

`period={kind:latest,value:null}` requests latest. Daily uses its exact YYYY-MM-DD; weekly uses the full **week start**, not a yearless label. `actual_period` identifies the returned data; `period_options` contains available days/weeks and resolution latest/exact/label/ambiguous/missing. Preserve existing visible missing/ambiguous fallback behavior when adapting board reads; a caller must never describe a fallback as the requested period. If no period is readable use period_missing. Nonperiod domains return null. Ranking results include historical delisted rows as the existing board does.

`order` names browse evidence/listed/title or explicit rank/published_at; `evidence_date` is the existing candidate order name. `order_version` pins the implemented deterministic order and tie-breaker (stable source key). Browsing and source rank are never called predicted commercial value. `offset` is zero-based; `limit` 1–200 supports current board pagination without a fixed page-count cap. `counts.total` is the domain/scope universe, `matched` is the complete filtered count, `returned` is the selected page count; `truncated` and `next_offset` describe pagination. `excluded` is a named filter diagnosis, not assumed disjoint arithmetic. If an existing capped rank cannot supply an exact total, downstream must model that explicitly before cutover; never invent a count.

`rows` is the candidate fact projection (`QueryRow`, existing `DramaInput` reused). It is **not** a replacement for all board data. `board: QueryBoardData|null` preserves full typed mirror records: catalog_rows, signals, posted, accounts, rs_rows, bill_orders and version rules. These reuse `mirror/contracts.py` models (including their closed nested source payload keys). `board.row_keys` defines the ordered page; the other arrays decorate that page and are not separate pages. No raw source/secret URL is introduced. Empty source language and unmatched posted records remain representable in board records even when no candidate identity is available. `facets` carries platform/language/basis/posted/rank/grade counts; each facet excludes its own selected dimension while retaining the others, exactly as current SQL. Posted-state counts exclude state filter but retain search. Rank counts remain global per existing board semantics.

`posted_status` is only the candidate's scoped truth assessment: posted/not_posted/unknown. Preserve original pub/sched/none/nomatch, post_count, sched_count, archived, account/source records in `board.posted`; do not collapse them into that assessment. Archived publications still count as published. `not_posted` requires complete matching, account/channel/window coverage. Neither missing joins nor no rows prove never posted.

Current board derived projections (ReelShort observations/growth/ledger joins, freshness and links) retain their existing public TS contracts in `server/pick-board/`; source records here make lossless adaptation possible but do not claim those calculations are implemented. T4/T5 must retain old read paths until each derived view proves pagination, ordering, facets, rule, error and fixed-version parity. Extend this versioned DTO explicitly if a derived field cannot be losslessly reconstructed; never introduce an untyped arbitrary payload.

The caller owns a single 10,000ms query budget. `budget_ms` may only shorten it; connection wait, SQL, enrichment and serialization consume the remainder. Internal calls honor the earlier ordinary-phase deadline. Cancellation releases resources. Source observation and mirror synchronization times are separate fields; historical evidence never becomes current because the mirror synced today.

## Checked final publication

`CheckedPublication` is an **internal extension-to-host payload**, not a public write endpoint. The host binds thread/run/message identity from trusted runtime authority; model input cannot create it. It contains content, status confirmed/partial/incomplete, per-claim `CheckedFact` (claim/status/evidence_refs/reason), up to two explicit result references, checker_version, correction_count 0–1 and checked_at. A confirmed fact needs evidence and a confirmed publication cannot contain unknown/contradicted facts. These DTO checks alone do not prove prose coverage: the checker must identify all hard factual claims, and unrecognized claims remain unconfirmed.

The public host message keeps its normal message ID/content envelope and receives server-written `additional_kwargs.pick_completion: CheckedMessageMetadata` (status/checker_version/checked_at/correction_count). Strip any client/model-supplied copy before publishing. T3 must emit the same metadata in stream and every history path. `run_status=success` or old answer-check notes do not imply a checked final message. Optional transient custom event `pick.processing` carries `PickProcessingEvent` (thread_id/run_id/stage=querying|checking|correcting|finalizing); emit only when the real stage begins and never replay it as a final guarantee. Old batch-only references allowing empty item_ids remain on the old reference protocol; the new ResultReference requires explicit selected items and does not replace that parser.

Only the checked content becomes the canonical host assistant message. Streaming, copy, all history reads, reconnect and subsequent context consume the same content/message ID. Repeated callbacks are idempotent. Provisional content is withheld from final surfaces. Partial output labels unknown facts explicitly and cannot repeat contradicted claims as truth. If no safe content exists, publish explicit incompletion. D10 reserves at most the final20s **inside** the effective existing deadline, starts no ordinary calls during that reserve, and never extends model-call quota or the one-correction cap. Cancellation also cannot bypass checking.

## Plans, confirmation and exports

Plan/row/export/link IDs use the existing opaque string convention (1–64 characters; implementations generate uuid4 hex). `PlanCreate` has request_id, title, IANA timezone, and up to100 rows. Each `PlanRowInput` has stable row_id and source identity/result/item, optional selection_id/account/channel/local_time/fold, copy_text and note. Missing execution fields may remain null in saved drafts. The server resolves title/theater/language and source_pin from owner-scoped evidence; clients cannot assert these facts. `Plan` adds id/version/timestamps and enriched `PlanRow[]`, including scheduled_at (offset-qualified UTC instant or null). Saving is user-confirmed, not model-authorized.

PATCH is a replacement of editable fields, not a JSON merge patch. Existing row IDs retain identity; new rows get new IDs; omitted rows are removed from that draft version. Duplicate row IDs fail. Every accepted mutation increments version once. Idempotency keys bind owner + operation + request body: same key/body returns the original receipt; different body conflicts. Failed/unknown network outcomes retry the same request ID. A version conflict preserves local edits and requires a fresh read and user reconciliation.

Time edits use the plan timezone, independent of browser timezone. Nonexistent DST local times cannot become executable; ambiguous times require fold0/1. Timezone changes require explicit keep_local_time/keep_instant plus preview; missing explicit choice is invalid. Source ids and source_pin remain immutable evidence for each row even when rules later change.

Preview pins plan version and current required source reads. `PlanCheck` contains each row's ready/blocked status, blockers/warnings, and current_pin separately from source_pin. Re-read current delisting, channel permission and required-source completeness before export commit. Any mandatory unknown/missing/read failure/denial blocks the **whole** execution export, retains all draft rows and explains each blocker; unrelated stale metrics only warn. Empty plans cannot export. Export request submits preview_id and expected_version; a mid-check edit yields version_conflict and requires new confirmation. A prior preview is never blanket authority for later rule changes.

Execution CSV columns in exact order: plan_id, plan_version, row_id, identity, source_result_id, source_item_id, title, theater, language, account, channel, local_time, timezone, scheduled_at, copy_text, note. UTF-8 BOM, RFC4180 escaping and formula-prefix neutralization apply to every user/source text cell (`= + - @`, leading tab/CR/LF and whitespace-prefixed formula). Preserve time zone/identity. Reference-list CSV keeps its old columns and clearly says reference-only. Export receipt binds immutable bytes/hash/plan_version/preview_id; download retry reads the same bytes after subsequent edits. Export generation/download never marks a row published.

## Feedback linkage

Feedback data stays in existing owner-private feedback versions; external sources remain read-only. `PlanLinkCommand` contains exact plan/row/revision, feedback version, post_key and explicit manual confirmation. The server verifies both objects are visible, the row belongs to that plan version, and the source post exists in the pinned version. `PlanLink` records provenance and manual/verified_external_id method, status confirmed/needs_review/conflict. Automatic linking is internal only, gated on verified unique cross-system ID propagation and compatible owner/source; title/date proximity is insufficient. Existing conflicting mappings are not overwritten; duplicate same confirmation is idempotent. Source revisions trigger revalidation and unresolved cases remain needs_review.

`GET /api/pick/feedback/posts` accepts ReviewQuery (optional feedback_version_id/account_id/channel/language/published_from/to plus offset/limit≤50). It lists actual owner-visible posts, including unlinked ones; ReviewPosts.status distinguishes ok/disabled/unavailable/auth_required. Unavailable totals are null, never0. ReviewPost supplies post_key, source identity/account/channel/title/language, publication/observation timestamps, observed/requested observation days, window_complete, nullable views/likes/comments, existing typed RevenueObservation entries, nullable PlanLink and source evidence refs. A disabled source never returns fabricated posts. Manual linking presents this exact post alongside the selected plan row before confirmation.

Existing feedback observation/revenue DTOs remain authoritative. Null, measured0, absent observation, insufficient observation window, unlinked and unattributed remain different states. Do not aggregate differing source/currency/metric/window or infer a conversion rate without a denominator. No production source activation is implied by these DTOs.

## D9 fixture and fingerprint freeze

`completion/model-acceptance-v1.json` locks exactly20 prompts, synthetic base records, per-case preconditions and expected behavior. File SHA-256: `04a5887b6de64b8b191aca821edb17d1928c6e13018b9581b7299335c7b6e668`. Preconditions are normative fixture overlays and run setup, not instructions to fabricate successful tool output. The baseline and new version receive identical seeded data/context and prompts.

`completion/model-config-fingerprint.json` defines the required config fingerprint format. It is intentionally a template: T11 must resolve real provider/model/parameters/effective limits and baseline/candidate SHAs, hash actual prompt/tool schemas and seeded QA data, then freeze the filled record before the first run. No secret values or production credentials belong in it. Fingerprint SHA-256 is over UTF-8 JSON with sorted keys, compact separators and no ASCII escaping; absent provider options are null and explicitly marked unsupported in the run ledger. Baseline/candidate intentional code/prompt/schema differences are recorded, all execution settings stay fixed.

Run ledger: `{case_id, phase:baseline|candidate, attempt:1, thread_id, run_id, started_at, finished_at, status, model_call_count, fingerprint_sha256, artifact_paths, expected_checks:[{expectation,passed,evidence}], failure}`. Failures/cancellation/timeouts still consume one of20 runs per phase. Total cap40 **Agent runs**, not API requests. No automatic extra attempts; no production writes. T1 executes zero product-model calls and does not claim model acceptance.

## Shared query reader (T4)

The Gateway now owns a separate `QueryReader` for `/api/pick/query` and the model's
`pick_query_data`. Configure **Gateway** `PICK_MIRROR_READER_URL` with the existing
restricted mirror reader role and `PICK_MIRROR_CA_PEM` with its validating CA.
These variables are independent of the host/writer `PICK_DATABASE_URL`; the
service never substitutes writer credentials. The URL accepts only PostgreSQL
and no query parameters. A missing reader is a typed `source_unavailable` error
for a paired mirror, while imported private catalogs continue on their own
provenance. The pool holds at most three connections, with five-second connect
and eight-second command backstops, no cached prepared statements, and every
request uses a read-only transaction with an absolute deadline. Only tests
construct the reader with TLS disabled against a disposable loopback cluster.

`rule_version=mirror-rules-v<N>` binds the immutable rules in mirror version N;
the server also verifies the exact catalog/knowledge batch pair. Private imported
catalogs keep `pick-rules-v1`. The original candidate tools explicitly preserve
`exclude_selected=true` and persisted card/notes shapes. The common tool is
read-only and creates no saved candidate reference. Observation-specific filters
remain on their existing candidate path.

Board domain projections additionally expose typed `rs_ids`, `rank_rows`,
`bill_rows`, `bill_totals`, `effective_sort`, `legacy_total`, `rank_limit`,
`rs_counts`, `growth_baseline`, `sources`, and `posted_stats`. These preserve
legacy linked-row, historical-rank, capped-growth and ledger semantics.
`facets.language_order` retains PostgreSQL order even when a language label is
numeric and JavaScript would reorder object keys. `row_keys` is the ordered
identity page (posted domain uses ledger record `sd`); ledger rank may decorate
multiple bills with the same canonical drama, so its returned count is the
number of bill records, not the number of unique drama keys.
