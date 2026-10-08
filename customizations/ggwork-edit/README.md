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
