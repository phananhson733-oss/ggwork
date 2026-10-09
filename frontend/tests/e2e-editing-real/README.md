# Real native editing acceptance

This opt-in suite uses the real authenticated frontend, Gateway, cloud text planner,
native worker, synthetic sources and output relay. It never intercepts API requests.
The separate `tests/e2e-editing` suite uses controlled fixtures and proves UI behavior only.

Prepare an isolated Gateway database/account and paired Apple Silicon worker. Start a
dedicated frontend with `DEER_FLOW_INTERNAL_GATEWAY_BASE_URL` pointing to the Gateway.
For local HTTPS, trust its test CA with `NODE_EXTRA_CA_CERTS`; never disable TLS validation.
Include the dedicated frontend origin in Gateway's explicit CORS configuration.

Keep the QA configuration outside the repository with owner-only file permissions:

```json
{
  "email": "isolated-qa@example.invalid",
  "password": "private-account-password",
  "deviceId": "paired-device-id",
  "directoryGrant": "drama",
  "directoryPath": ".",
  "receiveGrant": "incoming",
  "sourceFiles": [{ "path": "/synthetic/episode-01.mp4", "episode": 1 }],
  "existingTaskId": "real-task-id-for-history-identity",
  "completedTaskId": "real-directory-task-id",
  "profile": "hook",
  "language": "en",
  "durationSeconds": 8
}
```

The directory draft and history identity tests read real state without submitting anything.
Set the language to match the actual native model; English-only Whisper weights
require `en`, and correctly reject `auto`. Every upload run needs a fresh empty
receiving grant so prior originals remain untouched.
The directory delivery test reuses a genuinely completed task and makes no model call. The
submitted-file test creates one new task after its explicit Start click. Coordinate
the cloud-call budget with the native operator before running it. Retries are disabled;
inspect the attached `accepted-task` identity after any failure before deciding to rerun.
Do not rerun the creation test merely to repeat playback checks.

```sh
PLAYWRIGHT_BASE_URL=http://localhost:13308 \
EDITING_QA_CONFIG=/private/qa.json \
EDITING_EVIDENCE_DIR=/private/evidence \
pnpm exec playwright test --config playwright.editing-real.config.ts
```

Success requires actual video decoding and time advancement, HTTP 206 range bytes,
a complete browser download matching the immutable native result SHA-256/size, and
the exact task identity. Screenshots and sanitized JSON evidence are retained.
Traces, automatic screenshots and video recording are off because authentication
and one-time device tokens must not leak into artifacts. Revision checks here cover
the unsubmitted draft and original references; actual revised execution, worker
stop acknowledgment and retry need separate coordinated native evidence. For the
real disconnect test, stop the isolated worker first, then run with
`EDITING_QA_EXPECT_OFFLINE=1` and `--grep 'real worker offline'`. It waits up to
120 seconds for the actual heartbeat to expire, compares completed result metadata,
and verifies that the browser removes media actions. Reconnect the worker afterward.
Without that explicit operator coordination the disconnect test is skipped.

For actual version execution, provide `versionParentTaskId` for a completed upload
task and reserve one planner request, then set `EDITING_QA_CREATE_VERSION=1` and
run `--grep 'real linked version'`. The form retains existing sources and their
original paths, changes the opening instructions, uses English and one output,
and posts exactly one task without uploading again. It checks the completed new
MP4 and downloads the original again to confirm its hash is unchanged. Save the
`accepted-version.json` task ID as `versionTaskId` in a private QA config for
subsequent verification; that mode reuses the completed version and makes no
planner request.

For the actual chat tool card, have the host run the natural-language/Skill
conversation with the same QA owner, then supply `chatThreadId` and `chatTaskId`
and run `--grep 'real conversation tool card'`. The test reads the persisted
conversation and live task APIs without interception, follows the rendered card
to the exact task detail, and makes no model request. Version creation and chat
checks are skipped when their explicit prerequisites are absent.
