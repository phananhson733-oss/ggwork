# Editing delivery ledger

Recorded 2026-10-08. Source, automated checks, real runtime acceptance and deployment
are separate evidence layers. Open gates below are not passes.

## Source ledger

| Item | Recorded evidence |
| --- | --- |
| Original implementation base | `b17623966ef81e68c202a40a009306f49ffb03c9` |
| Refreshed review base | Business main `5b6372f9ee41b7c2f65589a2a9332e9bb7475221`, merged before final acceptance |
| Final business-main refresh | `3abc86b4` semantic Trends/Radar updates were merged before final reviewed source; the resulting editing-entry import order was fixed and frontend check/build rerun |
| Repository and branch | `phananhson733-oss/ggwork-deerflow`, remote `ggwork`, integration `codex/clip-workbench-integration`; upstream `origin` excluded |
| Specification | [#53](https://github.com/phananhson733-oss/ggwork-deerflow/issues/53), published body in [spec.md](spec.md), [task graph](task-graph.md) |
| Integration mounts | [integration.md](integration.md): packaged backend business logic and narrow frontend mounts |
| T8 packaging | Reviewed `cfb2482c`; final source `c08bd5e3` merged at `917db4bf`, then official manager regenerated both business snapshots and locked metadata |
| Reproducible acceptance | [acceptance runbook](acceptance.md); `scripts/editing-live-acceptance.py`; frontend `tests/e2e-editing-real/` |

## Task delivery

Implementation and both final review axes are complete. Tickets close through the
single business PR after its combined integration checks and merge.
The subsequent user review at `d88364c2` identified four additional reachable
recovery/alias bugs. Their explicit red/green repair evidence is recorded in
[review-repairs.md](review-repairs.md); all four fixes passed their original
reviewers' independent rechecks. The current native bundle and checks are below.
Issue closure is not a production deployment claim.

| Task | Implementation and evidence | Remaining acceptance boundary |
| --- | --- | --- |
| [T1 / #54](https://github.com/phananhson733-oss/ggwork-deerflow/issues/54) | Specification/task graph/mount decision recorded. Published spec byte equality and relative links checked at foundation. Workflow guidance moved to [agent workflow](../agents/overview.md) to preserve inherited instruction budgets. | PR integration/merge pending |
| [T2a / #62](https://github.com/phananhson733-oss/ggwork-deerflow/issues/62) | Generic exact-route bearer admission, live owner lookup and permission-free device principal; real Cookie/CSRF/device and revocation tests passed. No prefix bypass or session fallback. | Host prerequisite precedes T2; PR integration/merge pending |
| [T2 / #55](https://github.com/phananhson733-oss/ggwork-deerflow/issues/55) | Owner-scoped shared task repository and real host device-bearer admission; SQLite/PostgreSQL contract tests and actual host Cookie/CSRF/device flow passed. | Final source and packaged checks passed; PR integration/merge pending |
| [T3 / #56](https://github.com/phananhson733-oss/ggwork-deerflow/issues/56) | Real Apple Silicon CLI, actual pinned whisper weights, FFmpeg render/decode, grant checks, stopped acknowledgment and immutable outputs exercised. Matched development wheels installed in an isolated environment. | Final recovery/source-receipt snapshot and fresh standalone wheel passed; signing/notarization/daemon are not supplied |
| [T4 / #57](https://github.com/phananhson733-oss/ggwork-deerflow/issues/57) | Independent history/detail/revision UI and shared task cards; real browser task identity, directory draft, preview/download and file-upload pipeline passed. | Actual host `/clip-hook` query and browser live-card identity passed; PR integration/merge pending |
| [T5 / #58](https://github.com/phananhson733-oss/ggwork-deerflow/issues/58) | Real three-file upload/native ACK/verification; authenticated Range, complete download and hash checks passed. Real device-offline UI preserves completed history and disables access. | One Gateway process/replica constraint; no throughput/load claim |
| [T6 / #59](https://github.com/phananhson733-oss/ggwork-deerflow/issues/59) | Six successful configured Azure planner requests: five historical functional cases and one post-fix complete-unit narrative case. Earlier plans are candidly audited below; provider failures persist until explicit retry. | Actual chat and final recovery/source-receipt checks passed; PR integration/merge pending |
| [T7 / #60](https://github.com/phananhson733-oss/ggwork-deerflow/issues/60) | Accessibility/theme/recovery polish integrated; browser fixture checks and real authenticated screenshots recorded by frontend acceptance. | Final check/build and 3,038 unit tests passed; PR integration/merge pending |
| [T8 / #61](https://github.com/phananhson733-oss/ggwork-deerflow/issues/61) | Packaging, fresh locked host environment, paired native wheels, real directory/upload/media, external encoder fault and targeted retry, actual offline history checks passed as detailed below. | Final same-grant upload/source-receipt proof passed; PR integration/merge pending |

## Automated checks

| Check | Result and scope |
| --- | --- |
| Editing extension, final reviewed source | **266 passed**, with disposable PostgreSQL 17 and real native assets, including 30 fixed-content tests; no PostgreSQL/native skips |
| Legacy and current selection regressions | **178 passed**, SQLite/PostgreSQL query guards, save receipt, mirror replay, board replay, native source, catalog source and Radar tests |
| Strict backend blocking-I/O suite | **149 passed** on refreshed source |
| Packaging/acceptance CLI regression | **9 passed** (six CLI protocol cases plus three distribution checks); independently reviewed. HTTP/FFmpeg fakes in CLI tests are not live acceptance evidence. |
| Backend broad suite before business-main refresh | **18,487 passed, 171 skipped, 3 live tests deselected** after fixing new guide inventory/budget and setting the current virtualenv on PATH |
| Backend broad suite after refresh | **18,489 passed, 171 skipped, 3 deselected, 4 failures**. One Lark fork/join test failure reproduced on a clean pinned-main `5b6372f9` worktree. Three Docker lifecycle failures reported a missing daemon socket; isolated candidate/base reruns both skip them when Docker is unavailable. No unrelated source change made. |
| Focused failure audit | Candidate and clean pinned main each: **1 identical Lark failure, 3 Docker skips**. This is an explicit broad-suite limitation, not a green check. |
| Public Skill CI | Both changed `clip-highlight` and `clip-hook` packages passed against pinned main: **0 blockers, errors, warnings or info; 0 waived findings**. No waiver edits. |
| Guidance budgets | Against refreshed main: **0 errors**, existing warnings retained. Root guidance shrank relative to main; no budget increase. |
| Frontend | Source `045d2e34` after latest business-main merge: check, production build and full unit suite **3,038 passed / 45 skipped**. Real browser directory, upload, offline, conversation card, linked revision and final receipt/nonce upload passed. No intercepted API or fabricated media result used. |

## Actual native, model and browser evidence

All tasks used a new QA account, independent SQLite app home and explicit local
synthetic grants. The test model was real English-only whisper.cpp tiny.en,
SHA-256 `921e4cf8686fdd993dcd081a5da5b6c365bfde1162e72b08d75ac75289920b1f`.
Native `doctor` loaded it and verified the encoder on Apple Silicon. No production
media or database was accessed.

| Case | Real task and result |
| --- | --- |
| Directory and explicit recovery | `7fbabde040ef4827a8bc1b1b7590a548`: actual discovery, source hashes, ASR, configured cloud plan, native render and full decode. Output **720×1280 H.264/AAC, 8.966667 s, 239,883 bytes**, SHA-256 `75dd744522fa545d86ee60a10e737a4a428966069794f8b87d280123d6b1494a`. Real browser video time advanced; exact `206` Range and full download matched the receipt. |
| Real readiness refusal | Browser task `17e545699b4f40c7b43be8f8e276c7a1` uploaded three sources but requested `auto` language; the English-only worker correctly returned `model_language_unsupported`. No planner call occurred. Owner stopped the task; uploaded files were preserved. |
| Explicit English browser upload | With a fresh empty receiving grant, task `8e66b42a216f4e9ca5fc6594db2fb82b` completed three-file upload → native verification → ASR/cache → real planner → FFmpeg → browser playback/download. Output **1280×720, 8.9 s, 237,121 bytes**, SHA-256 `663715fd27e2048d3d3e9a5c2ac5b590b28f15094a65adf98d912d87cbf56a8a`. |
| Controlled external encoder failure | Task `fac46151d8ec4f898809aeadd176bded`: actual highlight plan requested two square outputs. An acceptance-only PATH wrapper exited 71 once at the second FFmpeg encode boundary; the first output was actually rendered and verified. Task became `partial`, not wholly failed or completed. No production/native module was monkeypatched. |
| Targeted retry and idempotency | Normal CLI retried only `out-2`; approved plan and `plan_attempt_id` stayed unchanged. First full result/hash remained identical; repeated retry request returned the same completed attempt. Both outputs passed actual Range/full download/hash/size/decode. Original sources stayed unchanged. |
| Actual conversation and shared card | Thread `cb86e2ffe39f4278af3c20c23e33d38a`: real `/clip-hook` input, model-selected `clip_get` for task `8e66b42a216f4e9ca5fc6594db2fb82b`, owner-scoped tool result and assistant reply. Browser rendered the live card and followed its exact task link. Two real model requests; no direct tool invocation used as acceptance. |
| Linked revision | Browser created `db149a978c094e16819ecb0812124d4b` from completed upload `8e66b42a216f4e9ca5fc6594db2fb82b`, retaining exact manifest/grant paths with explicit English and one output; no second upload. Actual planner/native/browser delivery produced 1280×720, 8.8 s, 234,279 bytes, SHA-256 `51d0628795c261cbbbfae72a2e87aed7437edfcfbc4630484c34fadb5abbecc9`. The original result metadata and full re-download hash stayed unchanged. |
| Final source identity fix | Task `f449fff9b09845c58568649b76c21eee` used the SAME `incoming-en` grant with three older files still present. Three fresh UUID receiving paths were disjoint from the prior upload, every native verified SHA matched selected local bytes, and all three task-scoped completed native receipts matched the frozen manifest. Actual planner/render/browser playback and full download passed: **1280×720, 8.966667 s, 239,289 bytes**, SHA-256 `92abbc92cf40979f9e27494ac83e473136489e1c13b1df79b0d2ba302e96746e`. Old and new originals were preserved. |
| Genuine stop and offline | Initial DNS-blocked task remained `stopping` while CLI was offline, then reached `stopped` after actual worker acknowledgment. Later, stopping the idle CLI and waiting for the real 90-second heartbeat expiry preserved completed task status/count/output identities while browser playback/download became unavailable. CLI was reconnected. |

The square retry outputs were **720×720 H.264/AAC**: first 5.466667 s,
144,263 bytes, SHA-256 `7c5da71202a40631f79da29281ede1287274780791fe9e15e3a1d3f10ccaef5c`;
second 5.333333 s, 140,145 bytes, SHA-256
`8bb42a9c9a6dcc99491a6a6178b904cc7a9de5b7aff2a97dabac37941b63f704`.
The second output has the new render attempt's artifact identity; the first keeps
its original identity. The retry retained the plan rather than requesting another
cloud plan.

## Environment and limits

Initial provider DNS lookups failed locally. Two failed invocation attempts were
observed and the worker was stopped to bound retries. Public DNS resolved the
approved provider; an isolated loopback CONNECT proxy allowed only that exact
provider origin and preserved end-to-end TLS verification. Only the isolated
Gateway received that proxy and three named provider variables. Native and browser
traffic used direct local TLS. System DNS and production configuration were not
changed. This workaround establishes the tested local transport, not a production
network repair.

The local TLS CA was trusted only in disposable test environments. Host sessions,
CSRF and real device bearer credentials were used; authentication and certificate
verification were not bypassed. Inherited database, app-home, source-sync and
feedback variables were cleared before Gateway startup; native source collection
and schedules remained off. Credentials, TLS private keys, raw provider logs and
browser state remain outside Git.

These short English synthetic cases establish functional behavior and measured
file metadata. They do not establish long-drama narrative quality, multilingual
ASR quality, broad model reliability, resource capacity or relay throughput.
Before the final review fixes, **seven successful provider HTTP 200 responses** were verified across
the runtime logs: five planner calls and two conversation model calls. Automatic
title generation was disabled only in the isolated QA config. These seven calls
are historical functional evidence; one separately authorized post-fix fixed-content
call passed below, making **eight** successful calls total (six planning, two chat).
The one-process/one-replica RAM relay limitation remains.
No Vercel/Railway deployment, signing, notarization or daemon installation was
performed. A push or merged PR would not change those facts.


## Distribution proof before the subsequent user review

The final native wheel was installed with its matching extension API wheel into a
new empty virtualenv: **13 packages**, with FastAPI, SQLAlchemy, LangChain and the
DeerFlow harness absent. Importing and running the real native CLI still passed.
The optional `gateway` extra declares its direct LangChain imports; a separate
clean environment installed that extra and imported the planner and tool symbols.
The locked host environment's **25 editing and 177 pick Python files** matched
the canonical source byte for byte. The new native environment's installed
editing Python files also matched the canonical source.

- Earlier `ggwork_edit-0.1.0-py3-none-any.whl`: SHA-256 `1117c362e8ee8762abdd9893752d2eb026af68a817e88a6fb316a051138d4ed6`
- `deerflow_extension_api-0.2.1-py3-none-any.whl`: SHA-256 `c54f6d145b3c439284db77bc5eccd284d55c9b4c327fc423817b9355e5330da1`

These are development wheels, not signed/notarized applications. The public API
wheel must be distributed with the native wheel. No production deploy was done.

## Final review follow-up

Standards review reported zero actionable findings. Spec review identified three:
lost task cards after host output budgeting, midpoint/repeated dialogue cuts, and
missing bounded long-input/fixed-content acceptance. The first two implementation
findings and long-input admission were fixed in `91439bc5`; latest business main
`3abc86b4` was merged before source `cf5c847f`, followed by the import-only fix
`045d2e34`. Both canonical package snapshots were regenerated through the official
extension manager. Follow-up Spec review approved this core implementation. Final Spec review then
verified the corpus/audit and independently hashed and fully decoded the fresh
real output: all three original findings resolved, zero remaining blockers.
Standards and the independent Python/corpus follow-up also approved.

- Real host budgeting now preserves compact receipts, stable task IDs, state and
  recovery actions. History, plan cuts and grants are paginated; full task data
  remains on the owner HTTP resource. No editing-tool budget exemption was added.
- Invalid midpoint/repeated cuts fail without storing a plan. Complete ASR units
  may be reordered; already delivered plans/outputs remain immutable.
- A complete-message 32,000-byte input policy rejects over-limit requests before
  model invocation, keeps the frozen source selection and returns actionable
  new-selection guidance. Model context capacity is unknown, not inferred.
- Post-fix shared tool/planner tests: **113 passed** across SQLite/PostgreSQL.
  Final full extension/native suite with all 15 fixed content cases:
  **266 passed, zero skips**. The initial full run hit a local template-config
  variable error; rerunning with an isolated minimal config passed without source
  changes or a real provider invocation.
- Final blocking-I/O: **149 passed**. Distribution/CLI/guidance tests:
  **22 passed**; guidance check against latest main: **0 errors, 12 existing
  warnings**. The final fixed content corpus itself passed **30 tests** across
  SQLite and PostgreSQL.
- Updated frontend: **3,038 passed / 45 existing skips**; check and production
  build passed after the import-order fix. The five focused presentation tests
  include actionable long-dialogue refusal.
- New matched development wheels from `045d2e34` were installed into a fresh
  **13-package** environment; all **25** editing Python files matched canonical
  bytes. Host-only frameworks were absent, and the actual native CLI doctor
  returned ready with pinned tiny.en weights and a synthetic grant. The local
  delivery ZIP contains both wheels, native README, SHA256SUMS and safe evidence.

The [15 annotated cases](quality-cases.md) include the [five retained-plan
assessments](retained-plan-audit.json): four historical Hook plans fail today's
boundary policy; the complete-unit highlight case passes. This does not modify
old evidence or turn decoding into a narrative-quality score. The new real
11-second conflict/stakes/challenge acceptance **passed**, with source ASR units,
plan order, actual native encoding and browser byte/playback proof in
[postfix-quality-evidence.json](postfix-quality-evidence.json). Task
`fda461b0acf240f9b2415a448089ecde` planned episode 2 `[0, 5.44]` then episode 3
`[0, 5.3]`, total 10.74 seconds; delivered 1280×720 H.264/AAC, 10.8 seconds,
286,920 bytes, SHA-256
`f99f50adce59a819a3f505da63780f954958eb51689d00c9788725e2fc682403`.
The browser's current time reached 1.497133 with no media error; exact Range 206
and full download matched the native receipt. All originals and isolated copies
retained their hashes. Old revision/chat replay passed 2/2, controlled UI fixtures
passed 6/6, and this new real-media browser case passed 1/1. The third Spec finding
was closed by the final Spec review using bounded input policy, annotated corpus,
retained-plan audit and one fresh content-aligned real output. This closes the limited
fixed-case gap, not broad narrative/multilingual evaluation. The broad-suite
Lark/Docker limitations and no-production-deployment boundary above remain.

## User-requested review repair and current development bundle

The later review of `d88364c2` found four real bugs beyond the earlier acceptance
coverage. Their sole-implementer fix is `27bae0d9`, with canonical code, tests,
native upgrade guidance and both managed snapshots regenerated by the official
extension manager. The same four original reviewers independently rechecked the
native stop state, directory provenance, unconfirmed-plan retry and Skill alias
boundaries; all four approved, with no new bug finding. See the detailed
[repair record](review-repairs.md) for each failing reproduction and recovery.

| Final repair check | Result |
| --- | --- |
| Entire editing extension | **319 passed, zero skips**, real SQLite and disposable PostgreSQL plus actual synthetic FFmpeg/whisper assets |
| Registered tool/Skill suite | **38 passed**, included in the extension count |
| Distribution, acceptance CLI and guidance tests | **22 passed** |
| Blocking-I/O | **149 passed**, two existing dependency deprecation warnings |
| Guidance delta against d88364c2 | **0 errors, 0 warnings** |
| Ruff lint / format | **38 canonical Python files passed** |
| Fresh native-wheel installation | **13 packages**, no host frameworks; **25 editing Python files** byte-identical to canonical source; real doctor ready |

The current development ZIP contains two local wheels, native README with the
same-version upgrade procedure, SHA256SUMS and safe evidence. Current hashes:

- `ggwork_edit-0.1.0-py3-none-any.whl`: **`92e7e56921616ca391ff0d96456bd2ddd8bc659d963bb3bafa2105df72b2c717`**
- `deerflow_extension_api-0.2.1-py3-none-any.whl`: **`c54f6d145b3c439284db77bc5eccd284d55c9b4c327fc423817b9355e5330da1`**

Stop an existing foreground worker and install **both** verified local wheels
with `pip install --force-reinstall`, or use a fresh virtualenv. Plain installation
of the same development version can retain old code. Keep the existing state
home and restart with it; no setup/reset, media deletion or daemon installation
is required. Package code and the native README are unchanged after `27bae0d9`;
later commits in this repair set record evidence only.

No frontend code, production deployment or paid provider call occurred in this
repair cycle. Prior native/browser/quality evidence and the total of eight
successful provider calls remain historical facts. The separate existing feedback
CI race and earlier broad-suite limits were not modified or relabeled as passing.
