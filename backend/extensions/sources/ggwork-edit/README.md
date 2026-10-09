# GGWork editing contract

The package persists one owner-scoped editing task for both page and conversation
entry points. It uses the host database but owns its private migration chain.
It does not load the selection mirror and does not require a conversation.

The authenticated browser pairs a device and receives a device token once. The
worker uses only this token on the exact contributed bearer routes. The database
stores its SHA-256 digest; revocation takes effect on the next request. Store the
native credential privately, never in command arguments or a model prompt.

Source selection remains waiting until every selected file has a device-verified
hash and duration and the bound Mac reports an authorized grant and readiness.
Directory discovery is restricted to an explicit grant-relative directory request.
The frozen manifest and requirements remain unchanged across failed-output retries.
Changing requirements creates a new task with `parent_task_id`.

Stop advances the publication fence. Running work stays `stopping` until the
worker acknowledges actual exit; device loss is not a stopped acknowledgment.
Partial successes remain indexed and immutable. Completion, device reachability,
and output access are separate: a heartbeat cannot prove an MP4 is playable.

The `highlight` and `hook` profiles are unavailable until a configured planner
sets `service.hook_available`. Owner settings may disable execution without
removing task history. Limits are initial policy defaults: 500 selected sources,
10 requested outputs, 5–180 seconds per output, 90-second device reachability and
attempt lease. They are not a measured throughput claim. Running attempts are
never silently reassigned after lease expiry; the same attempt may heartbeat to
resume reporting. Loss of the native attempt journal requires explicit recovery
work; this package does not infer that an offline process stopped.

Cloud planning admits at most 32,000 UTF-8 bytes for the complete role/content
messages (schema, requirements and all selected transcripts). This conservative
single-request policy never silently drops episodes or truncates dialogue. An
over-limit request fails before any model call; create a new task with fewer
selected episodes. The original task and materials remain available. The 500-file
selection limit does not promise that every such selection fits the planner.
Cuts must preserve complete punctuation-joined ASR units, without overlapping or
repeated ranges within an output. Complete units may be reordered for a Hook;
different outputs may reuse source ranges. Coarse ASR boundaries and punctuation
do not prove linguistic or narrative quality. Invalid cuts are rejected, not snapped.

Conversation tools return compact identity, state and recovery receipts so the
host context budget preserves the live card. The full task remains at its owner
HTTP resource. `clip_get` accepts `limit` (1–5), `offset`, `section=plan` with a task
ID to inspect every cut, or `device_id` to page authorized grants. Follow each
`next_offset` (and the overview's independent `device_next_offset`); summaries
do not silently stand in for the complete plan at explicit plan review.

`GET /api/editing/capabilities`, `/devices`, `/tasks`, and `/tasks/{id}` provide
current state. Creation, prepare, stop, retry and confirm-plan use the same
repository operations as tools. `/api/editing/worker/devices/{id}` exposes
heartbeat, preparation listing, directory discovery, verification, claim, detail
and fenced event reporting. It deliberately exposes no cloud filesystem API.

Tests: from repository root, select this checkout's package and extension API on
`PYTHONPATH`, then run `python -m pytest customizations/ggwork-edit/tests -q`.
HTTP tests use synthetic media metadata and temporary SQLite. Native decoding,
transcription, model quality, relay transport, signing and distribution must be
verified by their own integration work.

History is paginated with `limit` (1–100) and `offset`; responses include `total`
and `next_offset`, so older tasks remain reachable. Reads do not take the owner
mutation lock. Source-conversation links are accepted only from the authenticated
runtime's separately supplied thread identity; browser-supplied arbitrary links
are refused.

Set `EDIT_TEST_PG_URL` only to a disposable PostgreSQL cluster to run the same
suite on PostgreSQL as well as SQLite. The fixture creates and drops isolated test
databases. `test_gateway_auth.py` mounts the real host Gateway middleware and the
real editing authenticator, exercising session/CSRF pairing and constrained token
use without injecting a synthetic principal. Include this checkout's `backend`,
`backend/packages/harness`, and `backend/packages/extension-api` on `PYTHONPATH`
when using another checkout's Python environment.

Text planning is configured through the `ggwork-edit` plugin's `planner_model`
setting (for example `azure-pick` in `config.pick.example.yaml`). It uses the host's
`deerflow.models.create_chat_model` provider. Missing or invalid provider config
keeps planning unavailable while task history remains readable. No model
credentials are passed to the worker. Only requirements, stable output/media IDs,
and transcript timestamps/text are sent to the model; filenames and grant paths
are excluded. The provider must return strict JSON; invalid source references,
nonfinite/out-of-range times, mismatched profiles and omitted output IDs fail
without clamping or skipping. Output duration policy is within the larger of one
second or ten percent of the requested duration. This policy is not a quality
or throughput benchmark.

The worker sends `{attempt_id,fence,transcripts:[{media_id,segments:[{start,end,text}]}]}`
to `POST /api/editing/worker/devices/{device_id}/tasks/{task_id}/plan` using its
existing bearer token. The returned task includes
`plan:{profile,aspect_ratio,language,outputs:[{output_id,segments:[{media_id,start,end}]}]}`.
Segments are in playback order. Explicit `review_plan=true` waits for the shared
confirm-plan operation; other valid requests continue automatically. Continue
heartbeats during planning and plan review.

Original built-in `/clip-highlight` and `/clip-hook` Skills and the registered
aliases `$ggwork-edit/clip-highlight` and `$ggwork-edit/clip-hook` use the same
owner-scoped task operations. Per-owner Skill enabled state and current role
Skill/model authorization are checked in addition to the editing owner setting.
`POST /api/editing/skills/{name}` with `{skill_enabled:boolean}` stores the
owner's switch in the existing host Skill storage. Unknown aliases and paths do
not become commands. Registered aliases accept the same whitespace boundary as
slash Skills, including newline/tab or no arguments. No source-archive scripts, fonts or licensed resources are
bundled.

Conversation tools are `clip_submit`, `clip_get`, `clip_prepare`, `clip_stop`,
`clip_retry`, `clip_confirm_plan`, and `clip_change_version`. Single-task responses
are `{task:TaskReceipt}`; `clip_get` without a task ID returns paginated
`{items:TaskSummary[],devices:DeviceSummary[],capabilities:object,...}` so a waiting intent can bind
a newly paired Mac without asking the user for an opaque device identifier. The stable
UI link is `/workspace/editing/{task_id}`. Source thread identity comes from the
host lifecycle rather than model arguments. Changed requirements create a linked
new version and require fresh verification of selected source bytes.

Online file delivery uses the [bounded transient relay](docs/relay.md). Browser
uploads report progress only after the selected Mac acknowledges each chunk;
received bytes still require native media verification. Completed outputs expose
an authenticated same-origin streaming URL supporting a single HTTP byte Range,
plus an access preflight to distinguish missing files from an offline Mac. Both
sides must remain online. Deploy one Gateway process/replica; restart requires a
fresh transfer. Media bytes are never written to Gateway storage.

Native preparation failures are reported through the worker `preparation-error`
endpoint using fixed safe codes, not paths or raw exception text. They persist on
the waiting intent as `native_preparation_error` and a preparation reason. A
successful discovery/verification or explicit owner preparation clears the error
without creating another request. Admitted and terminal tasks reject these reports.

An output can publish only against its corresponding approved stored plan. The
planner validates source ranges and requested duration; publication checks the
encoded duration against that output's planned segment sum with a one-second
mux tolerance. Rendering retries retain the existing approved plan; transcription
or planning retries clear it and require a new current-attempt plan.
Stopping before plan confirmation requires `stage=planning` recovery; an
`output_ids` retry without a confirmed reusable plan returns 409 with that action.
The newly planned version waits for explicit confirmation again. Removing a bad
native-discovered source retains only the exact remaining identities on the same
Mac and grant, so successful directory files need no fabricated upload receipt.

The configured planner injects a live per-owner Skill/model profile policy through
`service.execution_profiles`. Browser and worker execution paths both enforce it,
including automatic admission and repeated claims. Policy lookup failure closes
admission. Reading history, stopping, heartbeats and terminal failure acknowledgments
remain available for safe cleanup when execution becomes unavailable.
