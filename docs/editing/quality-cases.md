# Editing content and long-input acceptance

The executable [fixed corpus](../../customizations/ggwork-edit/tests/fixtures/dialogue-quality-v1.json)
contains original synthetic dialogue, actual whisper.cpp tiny.en ASR segments,
source hashes, ten annotated cases and five retained real-plan assessments.
These are limited editorial expectations recorded during implementation, not a
human study or a second model's creative-quality score. Tests replay the external
model response through the real owner/task/planning HTTP boundary and temporary
SQLite/PostgreSQL databases. They do not make paid provider calls.

```sh
EDIT_TEST_PG_URL=postgresql://localhost:18543/postgres \
PYTHONPATH=backend:backend/packages/harness:backend/packages/extension-api:customizations/ggwork-edit \
backend/.venv/bin/python -m pytest customizations/ggwork-edit/tests/test_quality_cases.py -q
```

The ten fixed cases cover complete conflict → stakes → challenge, a complete-unit
Hook before its context, mid-utterance start/end, an ASR fragment ending with
“front of the”, A → B → A repetition, wrong language, unselected media, omitted
selected transcripts, and a structurally valid but narratively incomplete threat.
The last case is intentionally admitted: source/timing validity cannot certify
story meaning. The remaining five cases replay retained plans under today's
admission policy without changing their actual records or files.

The policy joins adjacent ASR segments until terminal `.?!。？！` punctuation
(ignoring trailing closing quotes/brackets), with the final transcript end as a
fallback. A cut must start and end at these exact observed unit boundaries.
Pauses inside a selected span remain intact. Every range must cite selected media;
overlaps/repeated ranges within one output are rejected even across intervening
sources. Complete units can be reordered for narrative context, and separate
requested outputs may reuse a range. No boundary snapping or invented word timing
occurs. ASR segmentation/punctuation may itself be coarse or wrong, so this is
observed-utterance protection, not universal grammatical completeness.

The [retained-plan audit](retained-plan-audit.json) found that **four of five**
earlier real plans would now be rejected: their Hook endings cut episode 3 before
its full 5.3-second unit. The highlight retry case used complete 5.44/5.3-second
units and passes. Those earlier native/browser checks still prove delivery,
hashes and recovery; they do not become new-policy quality passes. No historical
task, plan or output was rewritten. Fresh plans use the new validation policy;
retained approved plans remain unchanged for existing output-only retries.

The planned post-fix real content gate is one 11-second English Hook using episode
2 `[0, 5.44]`, then episode 3 `[0, 5.3]`: accusation/evidence → family stakes →
challenge, total 10.74 seconds. The actual new provider/native/browser result is
**pending**; deterministic fixture admission alone is not this real-media gate.

For long selections, the current strategy is an explicit single-request limit:
count the complete JSON role/content messages as UTF-8, including system prompt,
schema, requirements, all selected transcripts and unit metadata; admit at most
**32,000 bytes**. Over-limit input durably fails with `planner_input_too_large`
before any provider invocation and directs the owner to a new task with fewer
selected episodes. No automatic truncation, episode omission, summarization or
retry spends another model call. The frozen original selection stays available.
`capabilities.limits.max_planner_input_bytes` reports this policy; `max_sources=500`
is a file-selection bound, not a promise that all 500 transcripts fit.

The actual accepted QA provider configuration is alias `azure-pick`, Responses
API, reasoning `low`, output cap 32,000 tokens, request/stream timeout 300 seconds,
and zero SDK retries. Deployment name and credentials stay private. Its model
context window is **not declared/unknown**. The input byte limit is a conservative
initial product policy, not a tokenizer-accurate capacity guarantee or measured
long-drama throughput; smaller provider limits still surface as safe provider
failure. Broad long-drama narrative and multilingual ASR quality remain unmeasured.
