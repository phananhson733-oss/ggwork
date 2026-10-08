# Private content-plan drafts

T7 implements authenticated draft create/read/list/replacement-update routes under
`/api/pick/plans`. Preview, execution export and feedback links remain later
contracts; saving a draft never means it is executable, exported or published.

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
