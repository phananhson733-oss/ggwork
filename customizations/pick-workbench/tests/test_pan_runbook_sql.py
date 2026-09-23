"""The weekly pan check in docs/pick-workbench/supabase.md section 6: pan-check.sql, pan-redact.sql and the grep for raw files.

Until RealShort #67 scrubs v1 feed notes, a note holding a pan link is stored verbatim and copied onward: every catalog batch
the sync keeps, candidate snapshots, saved selections, and the host's checkpoints of the conversation that showed it. The
runbook's scripts run here as files from docs/, through psql, logged in as a stand-in for deerflow_app: not a superuser, owner
of the deerflow schema and every table in it. The contaminated data is written by the real sync, import, selection and
repository code; the host tables come from the host's own table definitions and langgraph's PostgresSaver.setup(). Every
location the check reports and every host branch holds a hit of its own, so disabling any one of them turns a test red. The
PostgreSQL half skips when PICK_TEST_PG_URL is unset.
"""

import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import httpx
import pg
import pytest
import pytest_asyncio
import yaml
from engines import HOST_JSON_SERIALIZER, host_engine
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker
from test_realshort_sync import RULES, TOKEN, feed_row

ROOT = Path(__file__).resolve().parents[3]
DOCS = ROOT / "docs/pick-workbench"
CHECK = DOCS / "supabase/pan-check.sql"
REDACT = DOCS / "supabase/pan-redact.sql"
RUNBOOK = DOCS / "supabase.md"
PLACEHOLDER = "[网盘信息已移除]"
# pan-check.sql's owner for a thread the DELETE route cannot find (require_existing): the runbook stops there.
NO_META = "(没有 threads_meta 行)"
# The runbook's broad pattern before this change; the scripts must keep matching all of it.
OLD_PATTERN = r"pan\.baidu|pan\.quark|aliyundrive|alipan|115\.com|123pan|lanzou|drive\.uc\.cn|cloud\.189\.cn|pan\.xunlei|提取码|提取碼|访问码|訪問碼|pwd="
# (table, key columns, JSON column): every JSON column of the ggwp tables, in the order of both scripts.
JSON_COLUMNS = [
    ("ggwp_drama_versions", "batch_id, identity", "payload_json"),
    ("ggwp_candidate_sets", "id", "ordered_items_json"),
    ("ggwp_candidate_sets", "id", "conditions_json"),
    ("ggwp_selections", "id", "snapshot_json"),
    ("ggwp_selection_commands", "owner_id, request_id", "receipt_json"),
    ("ggwp_answer_checks", "id", "notes_json"),
    ("ggwp_import_batches", "id", "validation_json"),
    ("ggwp_knowledge_versions", "batch_id, document_id", "metadata_json"),
]
# pan-redact.sql clears the first nine; the last two it never changes (the runbook stops and discusses).
LOCATIONS = [*(f"{table}.{column}" for table, _, column in JSON_COLUMNS), *(f"ggwp_knowledge_versions.{c}" for c in ("title", "text", "source_ref"))]
REDACT_OUTPUT = ["BEGIN", "CREATE FUNCTION", *["UPDATE n"] * 9, "DROP FUNCTION", "COMMIT"]
NOTHING = dict.fromkeys(LOCATIONS, 0)

# A note quoting a link, with escaped quotes and backslashes in the same string; the strings next to it hold escapes too
# (grade ends in a backslash right before note's opening quote) and must come through byte for byte.
LINK_NOTE = 'He said "see PAN.baidu.com/s/1AbC?pwd=x7k2", saved to C:\\dl\\'
CODE_NOTE = "访问码：8k2p，速存"
# Only a password and its separator, split by whitespace that JSON writes as the two characters \n and \t.
PASSWORD_NEWLINE = "密码\n：ab12"
PASSWORD_TAB = "密碼\t= cd34"
# U+000B, which JSON always writes as an escape, and U+00A0, which ensure_ascii escapes.
PASSWORD_VT = "密码" + chr(0x0B) + "：ab12"
PASSWORD_NBSP = "密码" + chr(0xA0) + "：cd34"
QUOTED_LABEL = 'KalosTV "日榜" 第1\\2'
TRAILING_BACKSLASH = "A\\"
SCOPE = "范围说明见 pan.quark.cn/s/scope1"
KNOWLEDGE_FILE = "网盘 pwd=ab12.md"
KNOWLEDGE_TEXT = "# 规则\n\n素材的提取码在群公告里"
# Near misses: 密码 without a separator is a common word in titles.
CLEAN_TITLE = "财富密码"
CLEAN_NOTE = "密码学入门，见 pan 字样也不算"
# Every non-ASCII character Unicode counts as whitespace, from Python's own tables. The pattern writes each as a branch of its
# own: a binary checkpoint column and a C-locale grep match them only as bytes, where [[:space:]] never sees them.
WIDE_SPACES = [chr(c) for c in range(0x80, sys.maxunicode + 1) if chr(c).isspace()]
# The pattern's other non-ASCII characters: the keywords and the full-width colon.
KEYWORD_CHARS = "提取码碼访问訪問密："
# The shells the runbook's greps may run in: the gateway image sets C.UTF-8, an ssh session may come up in C.
LOCALES = ("C", "C.UTF-8")


def _password(space: int) -> str:
    """A password whose only gap is one raw non-ASCII whitespace character."""
    return "密码" + chr(space) + "：ab12"


def _pattern(text: str) -> str:
    [pattern] = re.findall(r"^SELECT '([^']*)' AS pan \\gset$", text, re.M)
    return pattern


def _runbook_lines() -> list[str]:
    # split, not splitlines: U+0085, U+2028 and U+2029 inside the PAN= line are not line breaks.
    return [line.strip() for line in RUNBOOK.read_text(encoding="utf-8").split("\n")]


def _runbook_pan() -> str:
    [line] = [line for line in _runbook_lines() if line.startswith("PAN='")]
    return line


def _runbook_steps() -> dict[int, str]:
    """Section 6's numbered steps of the pan check, by number."""
    text = RUNBOOK.read_text(encoding="utf-8")
    section = text[text.index("**网盘片段核查") : text.index("## 7.")]
    parts = re.split(r"^(\d+)\. \*\*", section, flags=re.M)
    return {int(number): body for number, body in zip(parts[1::2], parts[2::2], strict=True)}


def _tool_results_line() -> str:
    """Step 1's find over every thread's externalized tool outputs: the threads with a hit, comma-separated."""
    [line] = [line.strip() for line in _runbook_steps()[1].split("\n") if line.strip().startswith("find /data ") and "/.tool-results/" in line]
    return line


def test_the_scripts_and_the_runbook_use_one_pattern_that_keeps_the_old_one():
    pattern = _pattern(CHECK.read_text(encoding="utf-8"))
    assert _pattern(REDACT.read_text(encoding="utf-8")) == pattern
    assert _runbook_pan() == f"PAN='{pattern}'"
    assert set(OLD_PATTERN.split("|")) <= set(pattern.split("|"))
    # Every backslash escape takes one or more backslashes, whatever the number of JSON layers.
    backslash = chr(92)
    assert backslash * 2 + "u" not in pattern.replace(backslash * 2 + "+u", "") and backslash * 2 + "[" not in pattern
    # Both gaps after 密码: ASCII whitespace, each non-ASCII whitespace character as its own branch, then the escapes.
    gap = "([[:space:]]|" + "|".join(WIDE_SPACES) + f"|{backslash * 2}+[bfnrtv]|{backslash * 2}+u[0-9a-fA-F]{{4}})*"
    assert pattern.count(gap) == 2
    assert {c for c in pattern if not c.isascii()} - set(KEYWORD_CHARS) == set(WIDE_SPACES)
    # The old inline queries are gone: the runbook holds the pattern once, and every grep uses $PAN.
    runbook = RUNBOOK.read_text(encoding="utf-8")
    assert runbook.count(pattern) == 1 and OLD_PATTERN not in runbook


def test_every_check_looks_at_the_database_and_the_disk():
    # Imports write the raw file before the rows: the database can be clean while the disk is not.
    steps = _runbook_steps()
    assert sorted(steps) == list(range(1, 9))
    assert "-f docs/pick-workbench/supabase/pan-check.sql" in steps[1] and "PAN='" in steps[1]
    assert 'grep -rlziE "$PAN" /data/pick' in steps[1]
    # The host's externalized tool outputs: the threads found on disk go into the check with -v disk_threads.
    assert _tool_results_line().endswith("| paste -sd, -") and "-v disk_threads=" in steps[1]
    assert "第 1 步" in steps[8] and "库和磁盘都查" in steps[8]
    # A leftover anywhere but the kept locations means redact and restart again, not only for candidate sets.
    assert "重做第 4、5 步" in steps[8] and "选择快照" in steps[8]
    assert "暂停使用" in steps[4]


# ---- a database like production: ggwp tables at head, the host's tables, all owned by a deerflow_app stand-in ----


def _libpq(url: str) -> str:
    return make_url(url).set(drivername="postgresql").render_as_string(hide_password=False)


def _create_host_tables(url: str) -> None:
    from deerflow.persistence.models.run_event import RunEventRow
    from deerflow.persistence.run.model import RunRow
    from deerflow.persistence.thread_meta.model import ThreadMetaRow
    from deerflow.persistence.user.model import UserRow
    from langgraph.checkpoint.postgres import PostgresSaver

    with PostgresSaver.from_conn_string(_libpq(url)) as saver:
        saver.setup()
    engine = create_engine(make_url(url).set(drivername="postgresql+psycopg"), json_serializer=HOST_JSON_SERIALIZER)
    try:
        with engine.begin() as conn:
            for table in (UserRow.__table__, ThreadMetaRow.__table__, RunRow.__table__, RunEventRow.__table__):
                table.create(conn)
    finally:
        engine.dispose()


def _admin(url: str, *statements: str) -> None:
    import psycopg

    with psycopg.connect(_libpq(url), autocommit=True) as conn:
        for statement in statements:
            conn.execute(statement)


@dataclass(frozen=True)
class Workbench:
    url: str
    owner: str
    password: str
    psql: str

    def fetch(self, statement: str, *params) -> list[tuple]:
        import psycopg

        with psycopg.connect(_libpq(self.url)) as conn:
            return conn.execute(statement, params).fetchall()

    def call(self, script: Path, *options: str) -> subprocess.CompletedProcess:
        """psql -f, logged in as the owner."""
        url = make_url(self.url)
        env = {key: value for key, value in os.environ.items() if not key.startswith("PG")} | {
            "PGHOST": url.host,
            "PGPORT": str(url.port or 5432),
            "PGDATABASE": url.database,
            "PGUSER": self.owner,
            "PGPASSWORD": self.password,
            "PGCLIENTENCODING": "UTF8",
            "PGCONNECT_TIMEOUT": "10",
        }
        command = [self.psql, "-X", "-w", "-A", "-F", "|", "-P", "footer=off", *options, "-f", str(script)]
        return subprocess.run(command, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, encoding="utf-8", timeout=120)

    def run(self, script: Path, *options: str) -> str:
        """psql -f; any error or warning fails the test."""
        result = self.call(script, *options)
        assert result.returncode == 0, result.stdout
        assert "ERROR" not in result.stdout and "WARNING" not in result.stdout, result.stdout
        return result.stdout

    def check(self, disk_threads: str = "") -> tuple[dict[str, int], list[tuple[str, str, str]]]:
        """pan-check.sql's two tables: rows with a hit per location, and threads with a hit, their owner and where the hit is."""
        lines = self.run(CHECK, "-v", f"disk_threads={disk_threads}").splitlines()
        assert lines[0] == "BEGIN" and lines[-1] == "COMMIT", lines
        header_counts, header_threads = lines.index("location|rows"), lines.index("thread_id|owner|found_in")
        counts = [line.split("|") for line in lines[header_counts + 1 : header_threads]]
        threads = [tuple(line.split("|")) for line in lines[header_threads + 1 : -1]]
        assert [location for location, _ in counts] == LOCATIONS
        return {location: int(rows) for location, rows in counts}, threads

    def redact(self) -> list[int]:
        """pan-redact.sql's UPDATE counts, in script order; the output is exactly what the runbook shows."""
        lines = self.run(REDACT).splitlines()
        assert [re.sub(r"^UPDATE \d+$", "UPDATE n", line) for line in lines] == REDACT_OUTPUT, lines
        return [int(line.split()[1]) for line in lines if line.startswith("UPDATE ")]

    def json_columns(self) -> dict[tuple, str]:
        """Every ggwp JSON value as stored, byte for byte."""
        stored = {}
        for table, keys, column in JSON_COLUMNS:
            for *key, text in self.fetch(f"SELECT {keys}, {column}::text FROM deerflow.{table}"):
                stored[(table, column, *key)] = text
        return stored

    def checkpoint_digest(self) -> list:
        tables = ("checkpoints", "checkpoint_blobs", "checkpoint_writes")
        return [self.fetch(f"SELECT count(*), md5(string_agg(t::text, '' ORDER BY t::text)) FROM deerflow.{name} AS t")[0] for name in tables]


@pytest.fixture
def workbench(pg_cluster, pg_template, pg_reader_role):
    psql = shutil.which("psql")
    if psql is None:
        pytest.fail("the runbook's scripts are psql scripts: put psql on PATH")
    owner, password = pg.unique_name("pan_owner"), secrets.token_hex(16)
    admin_url = pg_cluster.async_url(pg_cluster.url.database)
    # Created before the database and dropped after it, which takes the ownership along.
    _admin(admin_url, f"CREATE ROLE {owner} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD '{password}'")
    name = pg.unique_name("t")
    try:
        pg_cluster.create_database(name, template=pg_template)
        url = pg_cluster.async_url(name)
        _create_host_tables(url)
        _admin(
            url,
            f"ALTER SCHEMA deerflow OWNER TO {owner}",
            "DO $$ DECLARE t text; BEGIN FOR t IN SELECT tablename FROM pg_tables WHERE schemaname = 'deerflow' LOOP"
            f" EXECUTE format('ALTER TABLE deerflow.%I OWNER TO {owner}', t); END LOOP; END $$",
        )
        yield Workbench(url, owner, password, psql)
    finally:
        pg_cluster.drop_database(name)
        _admin(admin_url, f"DROP ROLE IF EXISTS {owner}")


@pytest_asyncio.fixture
async def service(workbench, tmp_path):
    from ggwork_pick.service import PickService

    engine = host_engine(workbench.url)
    svc = PickService(tmp_path / "files")
    await svc.initialize(async_sessionmaker(engine, expire_on_commit=False))
    yield svc
    await engine.dispose()


def _transport(rows, *, scope: str) -> httpx.MockTransport:
    """One feed page with rules, the way RealShort sends it; the scope lands in each batch's validation_json."""
    body = {"ok": True, "version": "pick-feed-v1", "capturedAt": "2026-09-23T03:00:00.000Z", "scope": scope, "total": len(rows)}
    body |= {"freshness": {"catalogImportedAt": "2026-09-22T03:19:21.327Z"}, "rules": RULES, "rows": rows, "nextCursor": None}

    def handler(request: httpx.Request):
        if request.headers.get("authorization") != f"Bearer {TOKEN}":
            return httpx.Response(401)
        return httpx.Response(200, json=body)

    return httpx.MockTransport(handler)


async def _sync(service, rows, *, scope: str = "test scope", **kwargs) -> dict:
    from ggwork_pick.sync import RealShortSync

    transport = _transport(rows, scope=scope)
    outcome = await RealShortSync(service, base_url="https://realshort.test", token=TOKEN, transport=transport, **kwargs).run("manual")
    assert outcome["status"] == "success", outcome
    return outcome


def _signal(i, **fields):
    return {"kind": "kd", "label": "KalosTV 日榜", "source_ref": f"ref:{i}", "observed_at": "2026-09-20", "rank": i, "grade": "", "note": "", **fields}


def _contaminated_feed() -> list[dict]:
    return [
        feed_row(1, signals=[_signal(1, label=QUOTED_LABEL, grade=TRAILING_BACKSLASH, note=LINK_NOTE)]),
        feed_row(2, signals=[_signal(2, note=CODE_NOTE)]),
        feed_row(3, title=CLEAN_TITLE, signals=[_signal(3, note=CLEAN_NOTE)]),
        feed_row(4),
        feed_row(6, signals=[_signal(6, note=PASSWORD_NEWLINE)]),
        feed_row(7, signals=[_signal(7, note=PASSWORD_TAB)]),
        feed_row(8, signals=[_signal(8, note=PASSWORD_VT)]),
        feed_row(9, signals=[_signal(9, note=PASSWORD_NBSP)]),
    ]


def _redacted(text: str, *hits: str) -> str:
    """What pan-redact.sql must leave: each string holding a hit replaced whole, every other byte as it was."""
    for hit in hits:
        text = text.replace(json.dumps(hit, ensure_ascii=False), json.dumps(PLACEHOLDER, ensure_ascii=False))
    return text


def _notes(row: dict) -> list[str]:
    return [signal["note"] for signal in row["signals"]]


def _identity(i: int) -> str:
    return json.dumps(["realshort-pick", f"row{i}", "en"], separators=(",", ":"))


def _knowledge(workbench) -> list[tuple]:
    return workbench.fetch("SELECT title, source_ref, text FROM deerflow.ggwp_knowledge_versions WHERE text = %s", KNOWLEDGE_TEXT)


@pytest.mark.asyncio
async def test_every_location_is_found_then_redacted_or_kept_by_design_and_the_workbench_keeps_working(workbench, service):
    from ggwork_pick.answer_check import check_answer
    from ggwork_pick.imports import Importer
    from ggwork_pick.repository import CATALOG_CACHE, PickRepository
    from ggwork_pick.selection import SelectionService

    alice = PickRepository(service.session_factory, "alice")
    selection = SelectionService(alice)
    # Batch A carries the notes and a scope quoting a link; a card, an empty query naming a pan domain, a saved choice and an
    # answer note use it. The save's request id holds a hit too: it is an id, kept by design.
    first = await _sync(service, _contaminated_feed(), scope=SCOPE)
    card = await selection.query({"limit": 20}, thread_id="thread-pan", run_id="run-1", call_id="call-1")
    empty = await selection.query({"query": "pan.baidu"}, thread_id="thread-pan", run_id="run-1", call_id="call-2")
    assert empty["items"] == [] and card["catalog_batch_id"] == first["catalog_batch_id"]
    linked = next(item for item in card["items"] if item["identity"] == _identity(1))
    receipt = await alice.save_selection("req-pwd=1", card["id"], [linked["item_id"]])
    answer_notes = check_answer("可以看看《提取码 x7k2》。", known_titles=set(), posted_checked=True)
    await alice.record_answer_check(thread_id="thread-pan", run_id="run-1", message_id="msg-1", notes=answer_notes)
    # A manual knowledge upload whose file name, source and text all hit.
    knowledge = await Importer(alice, service.data_dir).knowledge(KNOWLEDGE_TEXT.encode(), KNOWLEDGE_FILE, f"upload:{KNOWLEDGE_FILE}")
    # Receipts the code writes hold only ids, identity, request_id, status and version; a free-text field a later
    # receipt might carry must still be cleared, so one row stands in for it.
    synthetic = json.dumps({"request_id": "req-synthetic", "saved": [], "note": "提取码 x7k2"}, ensure_ascii=False)
    workbench.fetch(
        "INSERT INTO deerflow.ggwp_selection_commands (owner_id, request_id, payload_hash, receipt_json, created_at)"
        " VALUES ('alice', 'req-synthetic', 'h', %s::json, '2026-09-24T00:00:00.000000+00:00') RETURNING request_id",
        synthetic,
    )
    # The source is not fixed yet: batch B still carries the notes and becomes current; A stays, the card uses it.
    second = await _sync(service, [*_contaminated_feed(), feed_row(5)])
    assert (await alice.current_batch("catalog"))["id"] == second["catalog_batch_id"] != first["catalog_batch_id"]
    assert LINK_NOTE in _notes((await alice.catalog_rows(second["catalog_batch_id"]))[0])

    before = workbench.json_columns()
    counts, threads = workbench.check()
    assert counts == {
        "ggwp_drama_versions.payload_json": 12,
        "ggwp_candidate_sets.ordered_items_json": 1,
        "ggwp_candidate_sets.conditions_json": 1,
        "ggwp_selections.snapshot_json": 1,
        "ggwp_selection_commands.receipt_json": 2,
        "ggwp_answer_checks.notes_json": 1,
        "ggwp_import_batches.validation_json": 1,
        "ggwp_knowledge_versions.metadata_json": 1,
        "ggwp_knowledge_versions.title": 1,
        "ggwp_knowledge_versions.text": 1,
        "ggwp_knowledge_versions.source_ref": 1,
    }
    assert threads == []

    redacted = workbench.redact()
    after = workbench.json_columns()
    # Left: the request id (an id), the knowledge source (the document's identity) and its text (the whole rules).
    kept = {"ggwp_selection_commands.receipt_json": 1, "ggwp_knowledge_versions.text": 1, "ggwp_knowledge_versions.source_ref": 1}
    assert workbench.check() == (NOTHING | kept, [])
    assert redacted == [12, 1, 1, 1, 1, 1, 1, 1, 1]
    hits = (LINK_NOTE, CODE_NOTE, PASSWORD_NEWLINE, PASSWORD_TAB, PASSWORD_VT, PASSWORD_NBSP, "pan.baidu", *answer_notes, SCOPE, "提取码 x7k2", KNOWLEDGE_FILE)
    assert after == {key: _redacted(text, *hits) for key, text in before.items()}
    changed = {key for key in before if after[key] != before[key]}
    assert len(changed) == sum(redacted[:8])
    for key, text in after.items():
        assert PLACEHOLDER in text or key not in changed
        json.loads(text)
    assert _knowledge(workbench) == [(PLACEHOLDER, f"upload:{KNOWLEDGE_FILE}", KNOWLEDGE_TEXT)]

    # The gateway's batch cache still holds rows read before the redaction: the runbook restarts the gateway.
    assert LINK_NOTE in _notes((await alice.catalog_rows(second["catalog_batch_id"]))[0])
    CATALOG_CACHE.clear()
    for batch in (second, first):
        rows = {row["identity"]: row for row in await alice.catalog_rows(batch["catalog_batch_id"])}
        [signal] = rows[_identity(1)]["signals"]
        assert (signal["note"], signal["label"], signal["grade"]) == (PLACEHOLDER, QUOTED_LABEL, TRAILING_BACKSLASH)
        assert [_notes(rows[_identity(i)]) for i in (2, 6, 7, 8, 9)] == [[PLACEHOLDER]] * 5
        assert rows[_identity(3)]["title"] == CLEAN_TITLE and _notes(rows[_identity(3)]) == [CLEAN_NOTE]
    assert (await alice.batch_info(first["catalog_batch_id"]))["scope"] == PLACEHOLDER
    [document] = await alice.knowledge_documents(knowledge["id"])
    assert (document["title"], document["metadata_json"]) == (PLACEHOLDER, {"filename": PLACEHOLDER})
    detail = await selection.detail(card["id"], linked["item_id"])
    assert [evidence["note"] for evidence in detail["item"]["evidence"]] == [PLACEHOLDER]
    assert {result["id"] for result in await alice.results("thread-pan")} == {card["id"], empty["id"]}
    assert (await alice.result(empty["id"]))["conditions_json"]["query"] == PLACEHOLDER
    [saved] = await alice.selections()
    assert saved["identity"] == saved["snapshot_json"]["identity"] == linked["identity"]
    assert [evidence["note"] for evidence in saved["snapshot_json"]["evidence"]] == [PLACEHOLDER]
    assert (await alice.answer_checks("thread-pan"))[0]["notes"] == [PLACEHOLDER]
    assert (await alice.command_receipt("req-synthetic"))["note"] == PLACEHOLDER
    # Replays, 换一批 on the old card and a new save all still work on the redacted rows.
    assert await alice.save_selection("req-pwd=1", card["id"], [linked["item_id"]]) == receipt
    again = await selection.query({"exclude_previous": True}, thread_id="thread-pan", run_id="run-2", call_id="call-3", parent_result_id=card["id"])
    assert again["catalog_batch_id"] == first["catalog_batch_id"] and again["items"] == []
    other = next(item for item in card["items"] if item["identity"] != linked["identity"])
    assert (await alice.save_selection("req-2", card["id"], [other["item_id"]]))["saved"][0]["status"] == "created"

    # Idempotent: a second run changes nothing.
    settled = workbench.json_columns()
    assert workbench.redact() == [0] * 9
    assert workbench.json_columns() == settled


@pytest.mark.asyncio
async def test_identity_values_are_left_alone_and_the_check_keeps_reporting_them(workbench, service):
    from ggwork_pick.imports import Importer
    from ggwork_pick.repository import PickRepository

    alice = PickRepository(service.session_factory, "alice")
    rows = [
        {
            "source": "synthetic",
            "source_id": "k-pwd=1",
            "language": "en",
            "title": "Example",
            "signals": [{"kind": "rank", "source_ref": "x", "note": LINK_NOTE}],
        }
    ]
    batch = await Importer(alice, service.data_dir).catalog(json.dumps(rows).encode(), "json")
    [(identity,)] = workbench.fetch("SELECT identity FROM deerflow.ggwp_drama_versions")
    assert workbench.redact() == [1, 0, 0, 0, 0, 0, 0, 0, 0]
    [row] = await alice.catalog_rows(batch["id"])
    assert (row["identity"], row["source_id"], row["original"]["source_id"]) == (identity, "k-pwd=1", "k-pwd=1")
    assert _notes(row) == [PLACEHOLDER] and row["original"]["signals"][0]["note"] == PLACEHOLDER
    # The runbook stops here: a hit left in an identity is not the script's to change.
    assert workbench.check()[0]["ggwp_drama_versions.payload_json"] == 1
    assert workbench.redact() == [0] * 9


def test_the_scripts_cover_every_json_column_of_the_workbench(workbench):
    # SQLAlchemy's JSON is PostgreSQL json, not jsonb: stored text is kept byte for byte, which the redaction relies on.
    columns = workbench.fetch(
        "SELECT table_name, column_name, data_type FROM information_schema.columns"
        " WHERE table_schema = 'deerflow' AND table_name LIKE 'ggwp%%' AND data_type IN ('json', 'jsonb')"
    )
    assert sorted(columns) == sorted((table, column, "json") for table, _, column in JSON_COLUMNS)


@pytest.mark.asyncio
async def test_text_escaped_as_unicode_escapes_is_found_and_redacted(workbench, service):
    # The host serializer keeps non-ASCII verbatim; a writer with ensure_ascii would store \\uXXXX escapes instead.
    notes = [PASSWORD_TAB, "提取码 x7k2", 'clean "quoted" \\ text']
    escaped = json.dumps(notes, ensure_ascii=True)
    workbench.fetch(
        "INSERT INTO deerflow.ggwp_answer_checks (id, owner_id, thread_id, run_id, notes_json, created_at)"
        " VALUES ('check-1', 'alice', 'thread-x', 'run-x', %s::json, '2026-09-24T00:00:00.000000+00:00') RETURNING id",
        escaped,
    )
    assert workbench.check()[0]["ggwp_answer_checks.notes_json"] == 1
    assert workbench.redact() == [0, 0, 0, 0, 0, 1, 0, 0, 0]
    assert workbench.check()[0]["ggwp_answer_checks.notes_json"] == 0
    [(stored,)] = workbench.fetch("SELECT notes_json::text FROM deerflow.ggwp_answer_checks")
    # The escaped strings are replaced whole; the clean one keeps its escapes byte for byte.
    placeholder = json.dumps(PLACEHOLDER, ensure_ascii=False)
    assert stored == escaped.replace(json.dumps(notes[0]), placeholder).replace(json.dumps(notes[1]), placeholder)
    assert json.loads(stored) == [PLACEHOLDER, PLACEHOLDER, notes[2]]


# ---- host threads: binary checkpoints, found by pan-check.sql, removed only with the thread ----


def _put_thread(saver, thread_id: str, messages: list, *, title: str | None = None, writes: list | None = None, metadata: dict | None = None) -> None:
    from langgraph.checkpoint.base import empty_checkpoint

    values = {"messages": messages, **({"title": title} if title is not None else {})}
    versions = dict.fromkeys(values, "1")
    checkpoint = {**empty_checkpoint(), "channel_values": values, "channel_versions": versions}
    config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
    config = saver.put(config, checkpoint, {"source": "loop", "step": 1, **(metadata or {})}, versions)
    if writes:
        saver.put_writes(config, writes, "task-1")


# thread -> (owner, table the only hit is in). Every column the check reads has a thread whose only hit is there.
HOST_THREADS = {
    "thread-ckpt": ("alice", "checkpoints"),  # checkpoints.checkpoint: a string channel stays inline
    "thread-ckpt-meta": ("alice", "checkpoints"),  # checkpoints.metadata
    "thread-tool": ("alice", "checkpoint_blobs"),  # a tool output quoting a link, other case
    "thread-json-newline": ("bob", "checkpoint_blobs"),  # the tool output's JSON writes the newline as backslash-n
    "thread-real-newline": ("bob", "checkpoint_blobs"),  # a model answer with a real newline byte
    "thread-write-ascii": (None, "checkpoint_writes"),  # a pending write, ensure_ascii; no threads_meta row
    # Raw non-ASCII whitespace in a tool output (ensure_ascii off), so its UTF-8 bytes land in the blob as they are.
    "thread-blob-nbsp": ("alice", "checkpoint_blobs"),  # U+00A0
    "thread-blob-ideographic": ("bob", "checkpoint_blobs"),  # U+3000
    "thread-write-em": ("alice", "checkpoint_writes"),  # U+2003, in a pending write
    "thread-write-line": ("bob", "checkpoint_writes"),  # U+2028, in a pending write
    "thread-run-first": ("bob", "runs"),  # runs.first_human_message
    "thread-run-last": ("bob", "runs"),  # runs.last_ai_message
    "thread-run-kwargs": ("bob", "runs"),  # runs.kwargs_json
    "thread-run-meta": ("bob", "runs"),  # runs.metadata_json
    "thread-event-double": ("alice", "run_events"),  # run_events.content: a tool message holding JSON, serialized again
    "thread-event-double-ascii": ("alice", "run_events"),  # the same with an ensure_ascii tool output: two backslashes before u
    "thread-event-meta": ("alice", "run_events"),  # run_events.event_metadata
    "thread-meta-name": ("bob", "threads_meta"),  # threads_meta.display_name
    "thread-meta-json": ("bob", "threads_meta"),  # threads_meta.metadata_json
    "thread-clean": ("alice", None),
}
RAW_SPACES = {"thread-blob-nbsp": 0xA0, "thread-blob-ideographic": 0x3000, "thread-write-em": 0x2003, "thread-write-line": 0x2028}


async def _record_events(service, owners: dict[str, str]) -> None:
    """Tool messages written the way the host's run journal persists them: message.model_dump() through the event store."""
    from types import SimpleNamespace

    from deerflow.runtime.events.store.db import DbRunEventStore
    from deerflow.runtime.user_context import reset_current_user, set_current_user
    from langchain_core.messages import AIMessage, ToolMessage

    store = DbRunEventStore(service.session_factory)
    events = {
        "thread-event-double": [(ToolMessage(json.dumps({"note": PASSWORD_NEWLINE}, ensure_ascii=False), tool_call_id="e1"), None)],
        "thread-event-double-ascii": [(ToolMessage(json.dumps({"note": PASSWORD_NEWLINE}, ensure_ascii=True), tool_call_id="e2"), None)],
        "thread-event-meta": [(AIMessage("好的"), {"note": "提取码 x7k2"})],
        "thread-clean": [(AIMessage("《财富密码》在候选里。"), {"note": CLEAN_TITLE})],
    }
    for thread, messages in events.items():
        token = set_current_user(SimpleNamespace(id=owners[thread]))
        try:
            for message, metadata in messages:
                await store.put(
                    thread_id=thread, run_id=f"run-{thread}", event_type="llm.message", category="message", content=message.model_dump(), metadata=metadata
                )
        finally:
            reset_current_user(token)
    [(content,)] = await _scalar_rows(service, "SELECT content FROM deerflow.run_events WHERE thread_id = 'thread-event-double'")
    # Two layers of JSON: the newline is two backslashes and an n.
    assert "密码" + chr(92) * 2 + "n：ab12" in content


async def _scalar_rows(service, statement: str) -> list[tuple]:
    from sqlalchemy import text

    async with service.session_factory() as session:
        return [tuple(row) for row in (await session.execute(text(statement))).all()]


@pytest.mark.asyncio
async def test_each_host_column_finds_its_thread_until_the_thread_is_deleted(workbench, service):
    from deerflow.persistence.run.model import RunRow
    from deerflow.persistence.run.sql import RunRepository
    from deerflow.persistence.thread_meta.model import ThreadMetaRow
    from deerflow.persistence.thread_meta.sql import ThreadMetaRepository
    from deerflow.persistence.user.model import UserRow
    from deerflow.runtime.events.store.db import DbRunEventStore
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
    from langgraph.checkpoint.postgres import PostgresSaver
    from sqlalchemy import insert

    users = {"alice": str(uuid4()), "bob": str(uuid4())}
    owners = {thread: users[owner] for thread, (owner, _) in HOST_THREADS.items() if owner}
    link = json.dumps({"items": [{"evidence": [{"note": "资源 PAN.Baidu.com/s/1x"}]}]}, ensure_ascii=False)
    quiet = [HumanMessage("找剧")]
    escaped = ToolMessage(json.dumps({"note": PASSWORD_TAB}, ensure_ascii=True), tool_call_id="c3")
    raw = {t: ToolMessage(json.dumps({"note": _password(space)}, ensure_ascii=False), tool_call_id=t) for t, space in RAW_SPACES.items()}
    special = {
        "thread-ckpt": dict(messages=quiet, title="访问码 8k2p 的剧"),
        "thread-ckpt-meta": dict(messages=quiet, metadata={"note": "提取码 x7k2"}),
        "thread-tool": dict(messages=[*quiet, ToolMessage(link, tool_call_id="c1")]),
        "thread-json-newline": dict(messages=[ToolMessage(json.dumps({"note": PASSWORD_NEWLINE}, ensure_ascii=False), tool_call_id="c2")]),
        "thread-real-newline": dict(messages=[*quiet, AIMessage(PASSWORD_NEWLINE)]),
        "thread-write-ascii": dict(messages=quiet, writes=[("messages", [escaped])]),
        "thread-blob-nbsp": dict(messages=[*quiet, raw["thread-blob-nbsp"]]),
        "thread-blob-ideographic": dict(messages=[*quiet, raw["thread-blob-ideographic"]]),
        "thread-write-em": dict(messages=quiet, writes=[("messages", [raw["thread-write-em"]])]),
        "thread-write-line": dict(messages=quiet, writes=[("messages", [raw["thread-write-line"]])]),
        "thread-clean": dict(messages=[HumanMessage("有没有财富密码"), AIMessage("《财富密码》在候选里。")], title=CLEAN_TITLE),
    }
    with PostgresSaver.from_conn_string(_libpq(workbench.url)) as saver:
        # One checkpoint per thread: a blob version is written once, a second put would keep the first one's messages.
        for thread in HOST_THREADS:
            _put_thread(saver, thread, **special.get(thread, dict(messages=quiet)))
    engine = service.session_factory.kw["bind"]
    async with engine.begin() as conn:
        await conn.execute(insert(UserRow.__table__), [{"id": users[name], "email": f"{name}@example.test"} for name in users])
        names, meta = {"thread-meta-name": "访问码 8k2p", "thread-clean": CLEAN_TITLE}, {"thread-meta-json": {"note": "提取码 x7k2"}}
        await conn.execute(
            insert(ThreadMetaRow.__table__),
            [{"thread_id": t, "user_id": owner, "display_name": names.get(t), "metadata_json": meta.get(t, {})} for t, owner in owners.items()],
        )
        runs = {
            "thread-run-first": {"first_human_message": "密码：ab12 是这部剧的吗"},
            "thread-run-last": {"last_ai_message": PASSWORD_TAB},
            "thread-run-kwargs": {"kwargs_json": {"input": {"messages": [{"content": "提取码 x7k2"}]}}},
            "thread-run-meta": {"metadata_json": {"note": "访问码 8k2p"}},
            "thread-clean": {"first_human_message": "有没有财富密码", "last_ai_message": "《财富密码》在候选里。"},
        }
        for t, values in runs.items():  # one statement each: the rows set different columns
            await conn.execute(insert(RunRow.__table__).values(run_id=f"run-{t}", thread_id=t, user_id=owners[t], **values))
    await _record_events(service, owners)
    # The raw bytes of 密码 and the whitespace character are in the blob: only the pattern's literal branch matches them.
    for thread, space in RAW_SPACES.items():
        table = HOST_THREADS[thread][1]
        [(found,)] = workbench.fetch(
            f"SELECT bool_or(position(%s::bytea IN blob) > 0) FROM deerflow.{table} WHERE thread_id = %s", ("密码" + chr(space)).encode(), thread
        )
        assert found, thread

    listed = [(t, f"{owner}@example.test" if owner else NO_META, table) for t, (owner, table) in sorted(HOST_THREADS.items()) if table]
    assert workbench.check() == (NOTHING, listed)
    # pan-redact.sql never touches the host's tables.
    digest = workbench.checkpoint_digest()
    assert workbench.redact() == [0] * 9
    assert workbench.checkpoint_digest() == digest
    assert workbench.check()[1] == listed

    # What the thread DELETE route does to each store, as each thread's owner.
    runs_store, events_store, metas = (
        RunRepository(service.session_factory),
        DbRunEventStore(service.session_factory),
        ThreadMetaRepository(service.session_factory),
    )
    with PostgresSaver.from_conn_string(_libpq(workbench.url)) as saver:
        for thread, owner in owners.items():
            if thread != "thread-clean":
                saver.delete_thread(thread)
                await runs_store.delete_by_thread(thread, user_id=owner)
                await events_store.delete_by_thread(thread, user_id=owner)
                await metas.delete(thread, user_id=owner)
    # Without a threads_meta row the route answers 404: the runbook stops there.
    assert workbench.check() == (NOTHING, [("thread-write-ascii", NO_META, "checkpoint_writes")])
    assert workbench.fetch("SELECT count(*) FROM deerflow.checkpoints WHERE thread_id = 'thread-clean'") == [(1,)]


# ---- tool outputs the host keeps on disk: found in the container, listed by thread, removed with the thread ----


@pytest.mark.asyncio
async def test_tool_outputs_kept_on_disk_bring_their_threads_into_the_check_and_go_with_them(workbench, service, tmp_path, monkeypatch):
    from deerflow.agents.middlewares.tool_output_budget_middleware import _budget_content
    from deerflow.config.paths import Paths
    from deerflow.config.tool_output_config import ToolOutputConfig
    from deerflow.persistence.thread_meta.model import ThreadMetaRow
    from deerflow.persistence.thread_meta.sql import ThreadMetaRepository
    from deerflow.persistence.user.model import UserRow
    from langchain_core.messages import HumanMessage, ToolMessage
    from langgraph.checkpoint.postgres import PostgresSaver
    from sqlalchemy import insert

    if shutil.which("bash") is None:
        pytest.skip("needs bash")
    monkeypatch.syspath_prepend(str(ROOT / "backend"))
    from app.gateway.routers.threads import _copy_branch_user_data_sync, _delete_thread_data

    # The pick runtime keeps the host's defaults: a tool output over the threshold goes to .tool-results.
    pick = yaml.safe_load((ROOT / "config.pick.example.yaml").read_text(encoding="utf-8"))
    config = ToolOutputConfig(**(pick.get("tool_output") or {}))
    assert config.enabled and config.storage_subdir == ".tool-results"
    home, alice = tmp_path / "data", str(uuid4())
    paths = Paths(home)
    # A long candidate list with one note deep inside, externalized the way the host does it: the file holds the note, the
    # preview that stays in the checkpoint does not.
    items = [{"title": f"剧目{i}", "note": ""} for i in range(400)]
    items[200]["note"] = CODE_NOTE
    output = json.dumps({"items": items}, ensure_ascii=False)
    outputs = str(paths.sandbox_outputs_dir("thread-big", user_id=alice))
    preview, kind = _budget_content(output, tool_name="query_candidates", tool_call_id="c-big", outputs_path=outputs, config=config)
    assert kind == "externalized" and CODE_NOTE not in preview
    # Branching the conversation copies its user-data, externalized outputs included, to the new thread.
    assert _copy_branch_user_data_sync(paths, "thread-big", "thread-big-branch", user_id=alice) == "current_thread_best_effort"
    # A thread in the layout from before per-user directories, with no metadata row; its plain-text output breaks the line
    # between 密码 and the colon, so only a grep that reads the whole file (-z) finds it.
    legacy = home / "threads/thread-legacy/user-data/outputs/.tool-results/web_fetch-0123456789ab.log"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("\n".join([*(f"第{i}行" for i in range(2000)), PASSWORD_NEWLINE, "结束"]), encoding="utf-8")
    threads = ("thread-big", "thread-big-branch")
    with PostgresSaver.from_conn_string(_libpq(workbench.url)) as saver:
        for thread in threads:
            _put_thread(saver, thread, [HumanMessage("找剧"), ToolMessage(preview, tool_call_id="c-big")])
    async with service.session_factory.kw["bind"].begin() as conn:
        await conn.execute(insert(UserRow.__table__), [{"id": alice, "email": "alice@example.test"}])
        await conn.execute(insert(ThreadMetaRow.__table__), [{"thread_id": t, "user_id": alice, "display_name": None, "metadata_json": {}} for t in threads])

    # The database alone finds nothing. The find in the container names the threads; the check lists them with their owner.
    assert workbench.check() == (NOTHING, [])
    [found] = _shell(home, _tool_results_line())
    assert found == "thread-big,thread-big-branch,thread-legacy"
    owner = "alice@example.test"
    listed = [("thread-big", owner, ".tool-results"), ("thread-big-branch", owner, ".tool-results"), ("thread-legacy", NO_META, ".tool-results")]
    assert workbench.check(found) == (NOTHING, listed)
    # Without the disk's threads the check refuses to run.
    refused = workbench.call(CHECK)
    assert refused.returncode == 3 and "缺 -v disk_threads" in refused.stdout

    # Deleting a conversation, as its owner, removes its directory under the owner first, then its rows.
    metas = ThreadMetaRepository(service.session_factory)
    with PostgresSaver.from_conn_string(_libpq(workbench.url)) as saver:
        for thread in threads:
            assert _delete_thread_data(thread, paths=paths, user_id=alice).success
            saver.delete_thread(thread)
            await metas.delete(thread, user_id=alice)
    assert _shell(home, _tool_results_line()) == ["thread-legacy"]
    assert workbench.check("thread-legacy") == (NOTHING, [listed[2]])
    # The route cannot reach that one: step 6 deletes its files directly.
    [removal] = [line.strip() for line in _runbook_steps()[6].split("\n") if line.strip().startswith("find /data ") and "xargs -0r rm" in line]
    _shell(home, removal.replace("<线程>", "thread-legacy"))
    assert _shell(home, _tool_results_line()) == [] and workbench.check() == (NOTHING, [])
    assert not legacy.exists()


# ---- raw feed files: found with the runbook's grep, deleted, never overwritten ----


def _shell(home: Path, command: str, *, locale: str | None = None) -> list[str]:
    """A runbook command after the runbook's PAN= line, with a test directory standing in for the volume at /data."""
    script = _runbook_pan() + "\n" + command.replace(" /data", f" {home}")
    env = os.environ | ({"LC_ALL": locale} if locale else {})
    result = subprocess.run(["bash", "-c", script], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, encoding="utf-8", timeout=60)
    assert result.returncode in (0, 1), result.stdout  # grep exits 1 when nothing matches
    return sorted(result.stdout.split())


@pytest_asyncio.fixture
async def files_service(pick_db_url, tmp_path):
    from ggwork_pick.service import PickService

    engine = host_engine(pick_db_url)
    svc = PickService(tmp_path / "pick")
    await svc.initialize(async_sessionmaker(engine, expire_on_commit=False))
    yield svc
    await engine.dispose()


def _upload(note: str, *, ensure_ascii: bool) -> bytes:
    row = {"source": "synthetic", "source_id": "1", "language": "en", "title": "T", "signals": [{"kind": "r", "source_ref": "x", "note": note}]}
    return json.dumps([row], ensure_ascii=ensure_ascii).encode()


@pytest.mark.asyncio
async def test_raw_files_with_a_hit_are_found_and_deleting_them_is_safe(files_service):
    from ggwork_pick.imports import Importer
    from ggwork_pick.repository import PickRepository
    from ggwork_pick.sync import RealShortSync

    if shutil.which("bash") is None or shutil.which("grep") is None:
        pytest.skip("needs bash and grep")
    step_one = _runbook_steps()[1]
    [listing] = {line.strip() for line in step_one.split("\n") if line.strip().startswith("grep -") and line.strip().endswith('"$PAN" /data/pick')}
    [removal] = [line for line in _runbook_lines() if line.startswith("grep ") and "| xargs -0r rm" in line]
    shared, alice = PickRepository.shared(files_service.session_factory), PickRepository(files_service.session_factory, "alice")
    importer = Importer(alice, files_service.data_dir)
    # Each file holds exactly one hit, each in a different form.
    first = await _sync(files_service, [feed_row(1, signals=[_signal(1, note=PASSWORD_NEWLINE)]), feed_row(4)])  # backslash-n in the feed
    escaped_code = await importer.catalog(_upload("提取码 x7k2", ensure_ascii=True), "json")  # 提取码 as escapes
    escaped_nbsp = await importer.catalog(_upload(PASSWORD_NBSP, ensure_ascii=True), "json")  # keyword, U+00A0 and colon all escaped
    vertical_tab = await importer.catalog(_upload(PASSWORD_VT, ensure_ascii=False), "json")  # U+000B, which JSON always escapes
    csv = 'source,source_id,language,title\nsynthetic,2,en,"剧名 密码\n：ab12"\n'  # a quoted CSV field across two lines
    across_lines = await importer.catalog(csv.encode(), "csv")
    em_space = await importer.catalog(_upload(_password(0x2003), ensure_ascii=False), "json")  # a raw U+2003
    uploads = [first["catalog_batch_id"], escaped_code["id"], escaped_nbsp["id"], vertical_tab["id"], across_lines["id"], em_space["id"]]
    fixed = [feed_row(1), feed_row(4)]
    second = await _sync(files_service, fixed)
    paths = {row["id"]: row["raw_blob_path"] for row in await alice.batches()}
    assert "提取码" not in Path(paths[escaped_code["id"]]).read_text(encoding="utf-8")
    # The same list whether the ssh session's grep compares characters (C.UTF-8) or bytes (C).
    for locale in LOCALES:
        assert _shell(files_service.data_dir.parent, listing, locale=locale) == sorted(paths[batch_id] for batch_id in uploads), locale
    _shell(files_service.data_dir.parent, removal, locale="C")
    assert all(_shell(files_service.data_dir.parent, listing, locale=locale) == [] for locale in LOCALES)
    assert not any(Path(paths[batch_id]).exists() for batch_id in uploads)
    # The next sync prunes the old batches, one of whose files is already gone.
    clean = await _sync(files_service, [*fixed, feed_row(5)], keep_batches=1)
    statuses = {row["id"]: row["status"] for row in await shared.batches()}
    assert (statuses[first["catalog_batch_id"]], statuses[second["catalog_batch_id"]]) == ("pruned", "pruned")
    assert _shell(files_service.data_dir.parent, listing) == []
    # Nothing reads a raw file at runtime; only an import of the same content looks at it again, and rewrites a missing one.
    blob = Path((await shared.current_batch("catalog"))["raw_blob_path"])
    blob.unlink()
    assert (await _sync(files_service, [*fixed, feed_row(5)]))["catalog_batch_id"] == clean["catalog_batch_id"] and blob.exists()
    # An overwritten file fails that same import, which is why the runbook deletes and never rewrites.
    blob.write_bytes(b"[]")
    transport = _transport([*fixed, feed_row(5)], scope="s")
    refused = await RealShortSync(files_service, base_url="https://realshort.test", token=TOKEN, transport=transport).run("manual")
    assert refused["status"] == "failed" and "校验失败" in refused["error"]


def test_the_runbook_gives_the_sha256_of_its_pan_line(tmp_path):
    # The image has no docs/, so PAN is pasted. Copied from a rendered page, an invisible whitespace character can turn into a
    # space, a line break or another whitespace character of the same length; the digest shows any of them.
    if shutil.which("bash") is None or shutil.which("sha256sum") is None:
        pytest.skip("needs bash and sha256sum")
    step_one = _runbook_steps()[1]
    [command] = [line.strip() for line in step_one.split("\n") if line.strip() == 'printf %s "$PAN" | sha256sum']
    [expected] = re.findall(r'`printf %s "\$PAN" \| sha256sum` 应输出 `([0-9a-f]{64})`', step_one)
    pattern = _pattern(CHECK.read_text(encoding="utf-8"))
    assert expected == hashlib.sha256(pattern.encode()).hexdigest()
    assert expected != hashlib.sha256(pattern.replace(chr(0x2003), chr(0x2002)).encode()).hexdigest()
    for locale in LOCALES:
        assert _shell(tmp_path, command, locale=locale) == sorted([expected, "-"]), locale
