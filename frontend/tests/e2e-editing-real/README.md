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
  "completedTaskId": "real-directory-task-id",
  "profile": "hook",
  "durationSeconds": 8
}
```

The directory draft test checks real device state without submitting anything.
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
disconnect, stop acknowledgment and retry need separate coordinated native evidence.
