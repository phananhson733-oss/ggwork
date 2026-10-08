"""The weekly pan check's runbook, docs/pick-workbench/supabase.md section 6, as the tests read and run it.

Shared by test_pan_runbook_sql.py (the two SQL scripts, the host's threads), test_pan_runbook_obs.py (the scripts on the
observation radar's tables), test_pan_runbook_disk.py (the container's disk) and test_pan_runbook_text.py (the pattern, the
runbook's text). Shell commands are taken from the runbook text and
run with bash after its PAN= line, a test directory standing in for the gateway volume at /data.
"""

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

import httpx
from test_realshort_sync import RULES, TOKEN

ROOT = Path(__file__).resolve().parents[3]
DOCS = ROOT / "docs/pick-workbench"
CHECK = DOCS / "supabase/pan-check.sql"
REDACT = DOCS / "supabase/pan-redact.sql"
RUNBOOK = DOCS / "supabase.md"
# The shells the runbook's commands may run in: the gateway image sets C.UTF-8, an ssh session may come up in C.
LOCALES = ("C", "C.UTF-8")

CODE_NOTE = "访问码：8k2p，速存"
# Only a password and its separator, split by whitespace that JSON writes as the two characters \n and \t.
PASSWORD_NEWLINE = "密码\n：ab12"
PASSWORD_TAB = "密碼\t= cd34"
# U+000B, which JSON always writes as an escape, and U+00A0, which ensure_ascii escapes.
PASSWORD_VT = "密码" + chr(0x0B) + "：ab12"
PASSWORD_NBSP = "密码" + chr(0xA0) + "：cd34"

# (table, key columns, JSON column): every JSON column pan-redact.sql rewrites, in the order of both scripts.
JSON_COLUMNS = [
    ("ggwp_drama_versions", "batch_id, identity", "payload_json"),
    ("ggwp_candidate_sets", "id", "ordered_items_json"),
    ("ggwp_candidate_sets", "id", "conditions_json"),
    ("ggwp_selections", "id", "snapshot_json"),
    ("ggwp_selection_commands", "owner_id, request_id", "receipt_json"),
    ("ggwp_answer_checks", "id", "notes_json"),
    ("ggwp_import_batches", "id", "validation_json"),
    ("ggwp_knowledge_versions", "batch_id, document_id", "metadata_json"),
    ("ggwp_candidate_sets", "id", "data_as_of_json"),
    ("ggwp_sync_runs", "id", "details_json"),
    # Migration 0007: the observation radar's JSON columns. Frozen sets, judgment rows, alerts and decisions are rewritten
    # like the candidate snapshots: a hit is replaced, the identity-valued keys (REDACT_KEEPS_KEYS) never are.
    ("ggwp_candidate_sets", "id", "obs_as_of_json"),
    ("ggwp_obs_sets", "id", "frozen_inputs_json"),
    ("ggwp_obs_sets", "id", "summary_json"),
    ("ggwp_obs_states", "id", "labels_json"),
    ("ggwp_obs_states", "id", "flags_json"),
    ("ggwp_obs_states", "id", "metrics_json"),
    ("ggwp_obs_states", "id", "quality_note_json"),
    ("ggwp_obs_states", "id", "paste_row_json"),
    ("ggwp_obs_alerts", "id", "evidence_json"),
    ("ggwp_obs_decisions", "id", "payload_json"),
    ("ggwp_obs_identity_alias", "id", "evidence_json"),
    ("ggwp_obs_milestones", "id", "details_json"),
    ("ggwp_obs_runtime", "channel", "state_json"),
    ("ggwp_obs_batches", "id", "plan_json"),
    ("ggwp_obs_batches", "id", "summary_json"),
    ("ggwp_obs_batches", "id", "status_codes_json"),
    ("ggwp_obs_raw", "id", "params_json"),
    ("ggwp_obs_raw", "id", "data_json"),
    ("ggwp_gsc_slices", "id", "details_json"),
    ("ggwp_gsc_vchecks", "id", "slice_versions_json"),
]
# (table, text column) replaced whole by the placeholder when it hits: the knowledge file name, then 0007's drama titles and
# discovery terms (design 3.5).
REWRITTEN_TEXT = [
    ("ggwp_knowledge_versions", "title"),
    ("ggwp_obs_watch", "title"),
    ("ggwp_obs_states", "title"),
    ("ggwp_obs_states", "normalized_title"),
    ("ggwp_obs_identity_alias", "old_title"),
    ("ggwp_obs_identity_alias", "new_title"),
    ("ggwp_obs_discoveries", "term"),
    ("ggwp_obs_discoveries", "normalized_term"),
]
# Rows deleted when the column hits: a GSC query is part of the row's key, so it is dropped, not rewritten (D18).
DELETED_TEXT = [("ggwp_gsc_query_daily", "query")]
# Checked, never changed: the knowledge text (the whole rules) and source (its document id's input).
KEPT_TEXT = [("ggwp_knowledge_versions", "text"), ("ggwp_knowledge_versions", "source_ref")]
# Checked, never rewritten: the identities a query excluded, which 换一批 replays against, and the resolution hops of an
# immutable legacy-page snapshot (design 5.5).
KEPT_JSON_COLUMNS = [
    ("ggwp_candidate_sets", "id", "excluded_json"),
    ("ggwp_obs_legacy", "snapshot_id, raw_url", "hops_json"),
    # Feedback manifests/records are content-hashed and result evidence is immutable. Detect and stop for
    # owner-scoped incident handling; a generic in-place replacement would silently falsify their provenance.
    ("ggwp_feedback_versions", "id", "manifest_json"),
    ("ggwp_feedback_records", "version_id, table_id, record_id", "values_json"),
    ("ggwp_feedback_result_evidence", "result_id", "evidence_json"),
    ("ggwp_feedback_identity_links", "owner_id, source_record_id", "evidence_json"),
]
# JSON keys whose string values pan-redact.sql never replaces: changing an identity or a request id would break what refers to it.
REDACT_KEEPS_KEYS = ("identity", "source_id", "item_id", "citation_id", "request_id", "old_identity", "new_identity", "root_identity", "matched_identity")
# What pan-redact.sql clears, in the order it prints one UPDATE (then DELETE) line for each; then what it leaves.
CLEARED = [
    *(f"{table}.{column}" for table, _, column in JSON_COLUMNS),
    *(f"{table}.{column}" for table, column in REWRITTEN_TEXT),
    *(f"{table}.{column}" for table, column in DELETED_TEXT),
]
KEPT = [*(f"{table}.{column}" for table, column in KEPT_TEXT), *(f"{table}.{column}" for table, _, column in KEPT_JSON_COLUMNS)]
# pan-check.sql's first table: the cleared locations, then the ones the runbook stops at.
LOCATIONS = [*CLEARED, *KEPT]
REDACTED_NONE = [0] * len(CLEARED)
UPDATES = len(JSON_COLUMNS) + len(REWRITTEN_TEXT)
DELETES = len(DELETED_TEXT)
# Where each migration's columns are: a database still below it lists them as 0, and the redaction leaves their lines out.
ADDED_BY_0005 = ["ggwp_candidate_sets.data_as_of_json", "ggwp_sync_runs.details_json", "ggwp_candidate_sets.excluded_json"]
ADDED_BY_0007 = [location for location in LOCATIONS if location.startswith(("ggwp_obs_", "ggwp_gsc_", "ggwp_candidate_sets.obs_"))]


def cleared_at(revision: str) -> list[str]:
    """The locations pan-redact.sql clears on a database at this revision, in the order of its output lines."""
    missing = {"0004": ADDED_BY_0005 + ADDED_BY_0007, "0006": ADDED_BY_0007}.get(revision, [])
    return [location for location in CLEARED if location not in missing]


def feed_transport(rows, *, scope: str) -> httpx.MockTransport:
    """One feed page with rules, the way RealShort sends it; the scope lands in each batch's validation_json."""
    body = {"ok": True, "version": "pick-feed-v1", "capturedAt": "2026-09-23T03:00:00.000Z", "scope": scope, "total": len(rows)}
    body |= {"freshness": {"catalogImportedAt": "2026-09-22T03:19:21.327Z"}, "rules": RULES, "rows": rows, "nextCursor": None}

    def handler(request: httpx.Request):
        if request.headers.get("authorization") != f"Bearer {TOKEN}":
            return httpx.Response(401)
        return httpx.Response(200, json=body)

    return httpx.MockTransport(handler)


async def pull(service, rows, *, scope: str = "test scope", **kwargs) -> dict:
    """A manual RealShort sync of these feed rows, which must succeed."""
    from ggwork_pick.sync import RealShortSync

    transport = feed_transport(rows, scope=scope)
    outcome = await RealShortSync(service, base_url="https://realshort.test", token=TOKEN, transport=transport, **kwargs).run("manual")
    assert outcome["status"] == "success", outcome
    return outcome


def feed_signal(i, **fields):
    return {"kind": "kd", "label": "KalosTV 日榜", "source_ref": f"ref:{i}", "observed_at": "2026-09-20", "rank": i, "grade": "", "note": "", **fields}


def password(space: int) -> str:
    """A password whose only gap is one raw non-ASCII whitespace character."""
    return "密码" + chr(space) + "：ab12"


def pattern_of(text: str) -> str:
    [pattern] = re.findall(r"^SELECT '([^']*)' AS pan \\gset$", text, re.M)
    return pattern


def runbook_lines() -> list[str]:
    # split, not splitlines: U+0085, U+2028 and U+2029 inside the PAN= line are not line breaks.
    return [line.strip() for line in RUNBOOK.read_text(encoding="utf-8").split("\n")]


def runbook_pan() -> str:
    [line] = [line for line in runbook_lines() if line.startswith("PAN='")]
    return line


def runbook_steps() -> dict[int, str]:
    """Section 6's numbered steps of the pan check, by number."""
    text = RUNBOOK.read_text(encoding="utf-8")
    section = text[text.index("**网盘片段核查") : text.index("## 7.")]
    parts = re.split(r"^(\d+)\. \*\*", section, flags=re.M)
    return {int(number): body for number, body in zip(parts[1::2], parts[2::2], strict=True)}


def step_lines(step: int) -> list[str]:
    return [line.strip() for line in runbook_steps()[step].split("\n")]


def bash(home: Path, command: str, *, locale: str | None = None) -> subprocess.CompletedProcess:
    """A runbook command after the runbook's PAN= line, with home standing in for /data."""
    script = runbook_pan() + "\n" + re.sub(r"(?<![\w.-])/data(?![\w.-])", str(home), command)
    env = os.environ | ({"LC_ALL": locale} if locale else {})
    return subprocess.run(["bash", "-c", script], env=env, capture_output=True, encoding="utf-8", timeout=60)


def shell(home: Path, command: str, *, locale: str | None = None) -> list[str]:
    """The words a command prints; grep's "nothing matched" is not a failure."""
    result = bash(home, command, locale=locale)
    assert result.returncode in (0, 1), result.stdout + result.stderr
    return sorted(result.stdout.split())


@dataclass(frozen=True)
class Scan:
    code: int
    feed_files: list[str] | None  # None: the scan gave no list
    threads: str | None  # the line under [线程], what goes into -v disk_threads
    errors: str


def scan_script() -> str:
    """Step 1's pan_scan, defined and called as the runbook writes it."""
    lines = step_lines(1)
    start = lines.index("pan_scan() {")
    [end] = [i for i, line in enumerate(lines) if line.startswith("pan_scan;")]
    return "\n".join(lines[start : end + 1])


def scan(home: Path, *, locale: str | None = None) -> Scan:
    result = bash(home, scan_script(), locale=locale)
    lines = result.stdout.split("\n")
    [code] = [int(line.rsplit(" ", 1)[1]) for line in lines if line.startswith("pan_scan 退出码 ")]
    if "[线程]" not in lines:
        return Scan(code, None, None, result.stderr)
    feed_files = lines[lines.index("[原始 feed 文件]") + 1 : lines.index("[线程]")]
    after = lines[lines.index("[线程]") + 1]
    # BSD paste prints nothing for no threads, GNU paste an empty line.
    threads = "" if after.startswith("pan_scan 退出码 ") else after
    return Scan(code, sorted(feed_files), threads, result.stderr)


def clean_scan(home: Path, *, locale: str | None = None) -> bool:
    return scan(home, locale=locale) == Scan(0, [], "", "")
