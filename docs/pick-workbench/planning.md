# Private content-plan drafts

T7 implements authenticated draft create/read/list/replacement-update routes under
`/api/pick/plans`. T8 adds current-source previews and immutable execution
exports below; feedback links remain a later contract. Saving a draft never
means it is executable, exported or published.

The server resolves the owner through the existing Gateway principal and CSRF
boundary. No plan-write model tool is registered. New rows must name an owned
candidate result and exact item/identity; an optional selection ID must match
that same source. Source facts come from the saved candidate snapshot, not from
caller-supplied title, language, theater, pin or timestamp fields.

`PlanRow.source_pin` describes the immutable source-data/permission revision:
mirror-backed evidence uses `mirror-rules-v<N>` with the result's frozen mirror
version and catalog/knowledge IDs. The candidate's separate deterministic filter
policy remains unchanged on the linked source result. Private imports retain
their recorded policy key. A stored feedback sidecar's owner-scoped version is
preserved when present. Existing plan rows keep their snapshots even when source
batches or results later disappear. New source references still must resolve.

Private migration `0009 -> 0008` adds `ggwp_content_plans`,
`ggwp_content_plan_rows` and `ggwp_content_plan_commands`; it does not touch host
metadata or grant mirror-reader access. Omitted row IDs are tombstoned so they
cannot later be rebound to different source evidence. A reintroduced ID must
retain its exact original source. Listing uses one SQL statement for the total,
page headers and rows, so it cannot pair one version with another version's rows.

Writes use the existing SQLite immediate transaction/PostgreSQL owner-scoped
transaction lock. A command key is bound to its owner, operation, target and
normalized complete request. Exact retries return the original immutable Plan
receipt, even after later edits; changed content under the same key conflicts.
A fresh accepted mutation advances version once. PATCH requires
`expected_version` and replaces the entire editable row list in submitted order.
Conflicts return `version_conflict` and an owner-safe current version for the
existing frontend reconciliation flow.

Account/channel/time may be omitted in a saved draft. A supplied wall time must
be a real calendar value in an available IANA timezone: nonexistent DST times
are refused, repeated times require fold0/1, and out-of-range UTC conversions
fail without committing. Stored UTC timestamps have fixed microsecond precision.
Browser or machine timezone does not affect them.

Changing timezone requires an explicit mode. The frontend already converts wall
times during its preview, so the server validates submitted destination wall
times without converting them a second time. For `keep_instant`, every retained
active scheduled row must resolve to its original UTC instant; the frontend
locks local time/fold edits until that conversion is saved. `keep_local_time`
resolves the final submitted wall time in the new zone. A new or previously
unscheduled row has no old instant to preserve and uses its explicit new inputs.

Verification is synthetic and local: public API tests run on SQLite and
throwaway PostgreSQL; a separate complete loopback Gateway test uses real
session cookies/CSRF with no configured models. Restart, lost replies,
concurrent revisions, source loss, tombstone identity, row/input bounds and
schema privileges are covered. Managed extension packaging and deployment are
separate integration gates.

## Execution preview and immutable CSV

T8 adds `POST /plans/{id}/preview`, `POST /plans/{id}/exports` and
`GET /exports/{id}` under `/api/pick`. Both POSTs require the saved plan's
`expected_version` and an owner-scoped command key; export also requires its
owner-bound `preview_id`. Exact retries return their original receipts. A
blocked preview can be inspected again, but recovery uses a new preview command
so it actually rechecks repaired sources. No route creates a publication or
external write, and no model tool receives plan/export write authority.

Preview requires every row's account, target channel and valid persisted time,
plus the current exact identity's explicit `active` availability and `allowed`
channel permission. A missing record, unknown language/permission, failed read,
delist or denial blocks the entire execution file, while every draft row stays
editable. An empty plan cannot export. Same identity/account/channel duplicates
inside the draft are reported; this does not certify absence from historical
posting records or perform feedback linkage. Existing batch/source age notices
are warnings, not proof of permission or automatic export blockers.

Current source checks resolve one owner-checked QueryPin through common query,
then use a single bounded readonly mirror batch for at most 100 exact source
identities. T4b canonical identity and raw delist/deny projection is reused.
Missing raw records or platform rules cannot inherit imported affirmative
values. Mirror YouTube rules must be recognized: null, unknown or cautionary
values remain unknown; list-only permission also requires the actual raw row's
YouTube-list membership. Global `ok`/list labels never grant permission to a
canonical unknown. Other mirror target channels stay blocked until their
complete current-source contract exists. Private imports can carry explicit
supported-channel permissions without pretending to be a mirror.

Source checks and CSV rendering occur outside the plan write lock. One ten-second
absolute deadline covers each preview/export operation and all source reads;
client disconnect cancels work and releases connections. The final transaction
checks the plan revision again and fences source publication with brief
PostgreSQL SHARE locks on `ggwp_import_batches` then `pick_mirror.versions`, in
publisher order. Lock wait is at most two seconds and never exceeds remaining
operation time. A same-transaction pin read follows; SQLite's immediate write
transaction provides its commit fence. No source read uses the writer as a
fallback. A source change during validation, or a current pin differing from
the confirmed preview, requires a fresh preview. Export independently rechecks
all mandatory current facts even when the old preview was ready.

Private migration `0010 -> 0009` stores immutable preview snapshots and export
receipts/bytes in `ggwp_content_plan_previews` and `ggwp_content_plan_exports`.
The export bytes and command receipt commit atomically; later draft/source edits
never regenerate a successful file. Download retries only read the owner-bound
stored artifact. The receipt includes SHA-256, plan version, preview ID, row
count and a server-generated filename. Tables have no public/browser/mirror
reader grants. Files are UTF-8 with BOM, RFC4180 quoting and CRLF row endings.
Every text cell receives an apostrophe before formula/control prefixes,
including whitespace-prefixed formulas. Fixed columns are:

`plan_id, plan_version, row_id, identity, source_result_id, source_item_id, title, theater, language, account, channel, local_time, timezone, scheduled_at, copy_text, note`

The file preserves submitted plan order, plan timezone and its already-resolved
UTC instants, including an explicit DST fold. The reference-list CSV is
unchanged. Export means an execution file was generated; it never means a post
was published. Tests cover real SQLite/PostgreSQL transactions, source publishing,
concurrent edits, cancellation/disconnect, lost replies, restart/download
recovery, current mirror gaps and actual Gateway cookie/CSRF/owner boundaries.
