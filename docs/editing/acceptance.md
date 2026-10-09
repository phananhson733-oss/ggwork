# Editing packaging and acceptance

The editing extension is a development distribution. Source installation, native
execution, cloud planning, browser delivery and production release are separate
gates. Use synthetic media and an isolated loopback Gateway for acceptance. Never point
this workflow at a production database or a user's media directories.

## Reproduce the deployed package

From a clean checkout, create a private `config.yaml` from
`config.pick.example.yaml`, then use the extension manager:

```sh
cd backend
uv sync --locked --all-packages
DEER_FLOW_CONFIG_PATH="$PWD/../config.yaml" .venv/bin/deerflow extensions upgrade "$PWD/../customizations/ggwork-edit" --yes
DEER_FLOW_CONFIG_PATH="$PWD/../config.yaml" .venv/bin/deerflow extensions upgrade "$PWD/../customizations/pick-workbench" --yes
uv sync --locked --all-packages --extra postgres
.venv/bin/python -m pytest tests/test_editing_distribution.py -q
```

Use `install` instead of `upgrade` only when the distribution has not yet been
managed. Never edit `backend/extensions/sources/` as the source of a fix. Edit the
canonical customization, regenerate its snapshot with the manager, and commit the
snapshot, `pyproject.toml` and `uv.lock` together. The distribution regression test
compares every tracked customization file to its managed copy and checks the
three Skill copies in `Dockerfile.pick-gateway`. A locked install plus successful
Gateway startup is required in addition to that static comparison.

The optional `gateway` extra declares the extension's direct framework imports;
the default/native installation keeps only the lightweight API, HTTP and schema
dependencies. The native CLI needs **both** the editing wheel and the matching extension API
wheel, built from the same revision. The API package is not assumed published to
a public index. See the [native distribution instructions](../../customizations/ggwork-edit/ggwork_edit/worker/README.md).
No signing, notarization or automatic daemon installation is provided.

## Isolated live run

1. Create a dedicated app home, SQLite database and QA account through the real
   host initialization/login flow. Use a new native state home, empty output
   directory and a separate receive grant. Record process IDs and ports. Keep
   passwords, tokens, TLS private keys and raw provider logs outside Git with
   private permissions.
2. Start exactly one Gateway process and one replica. Disable catalog sync,
   feedback schedules and other writers. Build the child environment from safe
   variables; do not source the primary `.env`. Copy only the named Azure
   deployment/base URL/API-key variables into the Gateway environment. The
   native worker must never receive provider or host credentials.
3. Configure the actual text planner and both owner-admitted editing Skills.
   Pair through `/api/editing/devices` using host session Cookie and CSRF; use
   the resulting device bearer only in the native CLI. Run actual `doctor`,
   grant the synthetic directory and receiving directory, then `run`.
4. Use HTTPS with a valid trusted certificate. For a loopback test CA, trust it
   only in the disposable native environment's CA bundle and pass it to the
   HTTP acceptance client. Do not disable certificate verification or weaken
   worker URL validation. Configure the frontend's exact origin in
   `GATEWAY_CORS_ORIGINS` and its TLS upstream CA explicitly.
5. Execute the public-interface check below. It logs in, submits a directory
   task, observes the same task DTO, tests real Range delivery, verifies the
   complete downloaded SHA-256 against the immutable receipt and fully decodes
   the MP4. It does not replace browser acceptance, chat admission or narrative
   quality review. Its JSON evidence deliberately excludes credentials and raw
   provider responses.

```sh
backend/.venv/bin/python scripts/editing-live-acceptance.py \
  --gateway https://localhost:18443 --ca /private/qa/ca.pem \
  --credentials /private/qa/login.json --device DEVICE_ID \
  --grant synthetic --request-id unique-synthetic-run \
  --results /private/qa/results
```

Each invocation creates a private, uniquely named results subdirectory. Completed
MP4s are published there only after full hash, byte-length and decode checks;
failures write `passed: false` with a safe failure class and remove only that
run's temporary download. Earlier verified outputs and evidence are preserved.
The script refuses non-loopback Gateways before reading credentials.

The login JSON contains `email` and `password`. `--task-id TASK_ID` observes and
checks an existing task without creating another task. New submissions can
spend cloud-model tokens. Review the first failure before another call; never
substitute synthetic plans for failed live planning. A timeout or non-completed
state exits unsuccessfully and does not imply task cancellation. Stop the task
through the owner API and wait for actual native acknowledgment before teardown.

Browser acceptance must use the real Gateway and worker: directory entry,
file upload and native ACK/verification, ASR, actual model plan, FFmpeg render,
preview and download with Range, plus shared conversation/task identity. Test
stop/partial/retry with deterministic boundary faults separately from successful
live model cases. Fixture browser tests are only frontend regression evidence.
Original and delivered file hashes must remain unchanged through retries.

## Offline gates

Run editing and pick suites in separate pytest processes: the pick serializer
fixture is global within its test invocation. Use a disposable PostgreSQL
cluster; these suites create and remove test databases.

```sh
EDIT_TEST_PG_URL=postgresql://localhost:18543/postgres \
GGWORK_NATIVE_ASSETS=/private/qa/native-assets \
PYTHONPATH=backend:backend/packages/harness:backend/packages/extension-api:customizations/ggwork-edit \
backend/.venv/bin/python -m pytest customizations/ggwork-edit/tests -q
cd backend
PATH="$PWD/.venv/bin:$PATH" PYTHONPATH=. .venv/bin/python -m pytest -m 'not live' --ignore=tests/blocking_io tests/ -q
PATH="$PWD/.venv/bin:$PATH" PYTHONPATH=. .venv/bin/python -m pytest tests/blocking_io -q
```

Also run frontend check/unit/build, targeted pick browser regressions, and the
legacy pick filter/replay/save-receipt tests. Record exact revisions and separate
pre-existing broad-suite failures reproduced at the base from introduced failures.
See the [delivery ledger](delivery-ledger.md) for current evidence and open gates.

Run the [fixed content cases and retained-plan audit](quality-cases.md) alongside
the protocol suite. The real-content gate must also show a newly planned output
that satisfies the annotated conflict/stakes/challenge sequence and complete ASR
unit boundaries; decode success alone cannot close it. Model context is currently
unknown: enforce the reported complete-message byte limit, reject oversize input
before provider invocation, and keep all selected sources in the frozen manifest.
