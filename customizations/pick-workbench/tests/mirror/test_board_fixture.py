"""board_fixture (P3-1): the database the pick data board's integration tests and e2e read.

The board's tests are PostgreSQL only (skipped when PICK_TEST_PG_URL is unset): one board is built for the module
through the command line entry, read back as the reader it creates (and checked by P2's gates as the gateway), and taken
down at the end. The checks of board_world's rows themselves need no database. Every value is synthetic.
"""

import io
import json
import os
import re
import stat
from contextlib import closing
from pathlib import Path

import board_fixture as bf
import pytest
from gate_world import b64url

from ggwork_pick.mirror.publish import READER_ROLE_ENV

PASSWORD = "board-fixture-test-password"


def _connect(url: str):
    import psycopg

    return closing(psycopg.connect(url, autocommit=True))


def _admin_url(cluster, database: str) -> str:
    return cluster.url.set(drivername="postgresql", database=database).render_as_string(hide_password=False)


def _rows(url: str, statement: str, *params) -> list[tuple]:
    with _connect(url) as conn:
        return conn.execute(statement, params).fetchall()


@pytest.fixture(scope="module")
def board(pg_cluster, tmp_path_factory):
    work = tmp_path_factory.mktemp("board")
    url_file = work / "reader.url"
    out = io.StringIO()
    before = os.environ.get(READER_ROLE_ENV)
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv(bf.PASSWORD_ENV, PASSWORD)
        assert bf.main(["up", "--url-file", str(url_file), "--data-dir", str(work / "files")], out=out) == 0
    info = json.loads(out.getvalue())
    try:
        yield {
            "info": info,
            "output": out.getvalue(),
            "url_file": url_file,
            "reader": url_file.read_text(encoding="utf-8"),
            "env_before": before,
            "data_dir": work / "files",
        }
    finally:
        assert bf.main(["down", "--database", info["database"], "--role", info["role"]], out=io.StringIO()) == 0


def test_output_names_only_and_the_url_file_is_private(board):
    info = board["info"]
    assert set(info) == {"database", "role", "data_dir", "versions"}
    assert PASSWORD not in board["output"]
    assert stat.S_IMODE(board["url_file"].stat().st_mode) == 0o600
    assert f"{info['role']}:{PASSWORD}@" in board["reader"]
    assert board["reader"].endswith(f"/{info['database']}")
    # up leaves the process environment as it found it
    assert os.environ.get(READER_ROLE_ENV) == board["env_before"]


def test_reader_session_is_set_up_like_the_bootstrap(board):
    with _connect(board["reader"]) as conn:
        settings = {name: conn.execute(f"SHOW {name}").fetchone()[0] for name in ("default_transaction_read_only", "search_path", "TimeZone")}
        timeout = conn.execute("SHOW statement_timeout").fetchone()[0]
        who = conn.execute("SELECT current_user").fetchone()[0]
    assert settings == {"default_transaction_read_only": "on", "search_path": "pick_mirror", "TimeZone": "UTC"}
    assert timeout == "8s"
    assert who == board["info"]["role"]


def test_versions_in_every_state(board):
    ids = board["info"]["versions"]
    rows = dict(_rows(board["reader"], "SELECT id::int, status FROM pick_mirror.versions"))
    assert rows == {ids["dropped"]: "dropped", ids["v1"]: "published", ids["v2"]: "published", ids["building"]: "building", ids["failed"]: "failed"}
    current = _rows(board["reader"], "SELECT id::int FROM pick_mirror.versions WHERE status = 'published' ORDER BY published_at DESC, id DESC LIMIT 1")
    assert current == [(ids["v2"],)]


def test_reader_reads_published_versions_only(board):
    ids = board["info"]["versions"]
    query = (
        "SELECT v.id::int, n.nspname IS NOT NULL, coalesce(has_schema_privilege(n.oid, 'USAGE'), false)"
        " FROM pick_mirror.versions v LEFT JOIN pg_namespace n ON n.nspname = v.schema_name"
    )
    seen = {row[0]: row[1:] for row in _rows(board["reader"], query)}
    assert seen == {
        ids["dropped"]: (False, False),
        ids["v1"]: (True, True),
        ids["v2"]: (True, True),
        ids["building"]: (True, False),
        ids["failed"]: (False, False),
    }


def test_reader_may_not_read_control_nor_write(board):
    import psycopg

    with _connect(board["reader"]) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("SELECT * FROM pick_mirror.control")
        with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
            conn.execute("CREATE TEMP TABLE scratch (n int)")


RADAR_VIEWS = ("sets", "states", "links", "discoveries", "run_status")


def test_the_radar_rows_read_as_the_reader_are_contract_rows(board):
    """board_obs's world through migration 0007's views and grants: what the trends and search tabs read (TR-24)."""
    import board_obs

    from ggwork_pick.observe.contract_views import VIEW_COLUMNS, VIEW_ROW_MODELS

    world = board_obs.view_rows()
    with _connect(board["reader"]) as conn:
        for view in RADAR_VIEWS:
            names = [column.name for column in VIEW_COLUMNS[view]]
            cursor = conn.execute(f"SELECT {', '.join(names)} FROM pick_obs.{view}")
            rows = [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]
            assert len(rows) == len(world[view]), view
            for row in rows:
                VIEW_ROW_MODELS[view].model_validate(row)


def test_the_reader_reads_no_radar_table(board):
    import psycopg

    with _connect(board["reader"]) as conn, pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute("SELECT 1 FROM deerflow.ggwp_obs_sets")


def test_the_two_published_versions_differ(board):
    ids = board["info"]["versions"]
    titles, rules = {}, {}
    for name in ("v1", "v2"):
        schema = f"pickm_v{ids[name]:06d}"
        titles[name] = _rows(board["reader"], f"SELECT title FROM {schema}.catalog_rows WHERE row_key = 'c-1'")[0][0]
        rules[name] = _rows(board["reader"], f"SELECT value -> 'platformRules' -> 'shortmax' ->> 'yt' FROM {schema}.meta WHERE key = 'rules'")[0][0]
    assert titles == {"v1": bf.V1_TITLE, "v2": bf.V2_TITLE}
    assert rules == {"v1": "ok", "v2": "no"}
    platforms = _rows(board["reader"], f"SELECT row_key, platform FROM pickm_v{ids['v2']:06d}.catalog_rows ORDER BY row_key")
    theaters = (*bf.PLATFORMS, *(bf.LIST_ONLY_PLATFORM for _ in bf.LIST_ONLY_ROWS))
    assert platforms == list(zip(("c-1", "c-2", "c-3", "c-4", "c-5", "c-6"), theaters, strict=True))


def test_each_published_version_is_paired_with_its_v1_batches(pg_cluster, board):
    info = board["info"]
    admin = _admin_url(pg_cluster, info["database"])
    pairs = _rows(admin, "SELECT id::int, agent_catalog_batch_id, agent_knowledge_batch_id FROM pick_mirror.versions WHERE status = 'published'")
    assert len(pairs) == 2 and all(catalog and knowledge for _, catalog, knowledge in pairs)
    batches = dict(_rows(admin, "SELECT id, status FROM deerflow.ggwp_import_batches"))
    assert all(batches[catalog] == "published" and batches[knowledge] == "published" for _, catalog, knowledge in pairs)
    v2_catalog = next(catalog for version, catalog, _ in pairs if version == info["versions"]["v2"])
    identities = {row[0] for row in _rows(admin, "SELECT identity FROM deerflow.ggwp_drama_versions WHERE batch_id = %s", v2_catalog)}
    assert json.dumps(["realshort-pick", b64url("c-1"), "英语"], ensure_ascii=False, separators=(",", ":")) in identities


def test_series_for_one_canonical_drama(board):
    state = _rows(board["reader"], "SELECT through::text, trimmed_before::text FROM pick_mirror.series_state WHERE id = 1")
    assert state == [("2026-09-23", "2026-06-25")]
    series = _rows(board["reader"], "SELECT drama_id, cardinality(days) FROM pick_mirror.series")
    assert series == [(bf.SERIES_DRAMA, 3)]


def test_reelshort_ids_are_shaped_like_book_ids_and_resolve_as_realshort_does(board):
    """reelshortId (request.ts) takes [a-z0-9]{6,40} only; a sibling resolves to its canonical, rs0007 to none."""
    schema = f"pickm_v{board['info']['versions']['v2']:06d}"
    drama_ids = [row[0] for row in _rows(board["reader"], f"SELECT drama_id FROM {schema}.rs_rows ORDER BY drama_id")]
    assert drama_ids == ["rs0001", "rs0002", "rs0003", "rs0004", "rs0005"]
    assert all(re.fullmatch(r"[a-z0-9]{6,40}", drama_id) for drama_id in drama_ids)
    canonical = dict(_rows(board["reader"], f"SELECT id, canonical_id FROM {schema}.rs_ids WHERE id IN ('rs0006', 'rs0007')"))
    assert canonical == {"rs0006": "rs0001", "rs0007": None}
    bills = _rows(board["reader"], f"SELECT book_id, canonical_id FROM {schema}.rs_bill_orders ORDER BY bill_date")
    assert bills == [("rs0006", "rs0001"), ("rs0001", "rs0001"), ("book-x", None), ("rs0004", "rs0004")]
    counts = _rows(board["reader"], f"SELECT value -> 'rs_ids' FROM {schema}.meta WHERE key = 'counts'")
    assert counts == [(7,)]


def test_v1_identities_name_the_mirror_row_keys(pg_cluster, board):
    """The agent's v1 identities and the board's row keys are the same keys: replay (P4-2) maps one onto the other."""
    info = board["info"]
    admin = _admin_url(pg_cluster, info["database"])
    schema = f"pickm_v{info['versions']['v2']:06d}"
    catalog = _rows(admin, "SELECT agent_catalog_batch_id FROM pick_mirror.versions WHERE id = %s", info["versions"]["v2"])[0][0]
    identities = {row[0] for row in _rows(admin, "SELECT identity FROM deerflow.ggwp_drama_versions WHERE batch_id = %s", catalog)}
    keys = {row[0] for row in _rows(board["reader"], f"SELECT row_key FROM {schema}.rs_rows WHERE has_signal")}
    for key in keys:
        assert json.dumps(["realshort-pick", b64url(key), "英语"], ensure_ascii=False, separators=(",", ":")) in identities


def test_each_version_has_the_snapshot_of_its_moment(board):
    """v1 was cut before RealShort's 2026-09-23 snapshot, v2 after it: the curve of v1 stops a day earlier."""
    ids = board["info"]["versions"]
    days = dict(_rows(board["reader"], "SELECT id::int, latest_snapshot::text FROM pick_mirror.versions WHERE status = 'published'"))
    assert days == {ids["v1"]: "2026-09-22", ids["v2"]: "2026-09-23"}
    for name in ("v1", "v2"):
        schema = f"pickm_v{ids[name]:06d}"
        meta = _rows(board["reader"], f"SELECT value #>> '{{}}' FROM {schema}.meta WHERE key = 'latestSnapshot'")
        assert meta == [(days[ids[name]],)]


@pytest.mark.asyncio
@pytest.mark.parametrize("name, title, youtube", [("v1", bf.V1_TITLE, "ok"), ("v2", bf.V2_TITLE, "no")], ids=["v1", "v2"])
async def test_published_versions_pass_the_mirror_gates(pg_cluster, board, name, title, youtube):
    """P2's own gates (G3-G9) over each published version: its manifest's counts and control totals are what its rows
    give, and its v1 pull names the same candidates. A run could publish every version of this board."""
    import gate_world as gw

    from ggwork_pick.mirror.connection import dsn_from_url, open_dedicated
    from ggwork_pick.mirror.gates import run_mirror_gates

    world = bf.board_world(title, youtube, bf.AS_OF[name])
    schema = f"pickm_v{board['info']['versions'][name]:06d}"
    conn = await open_dedicated(dsn_from_url(pg_cluster.async_url(board["info"]["database"])))
    try:
        gates = await run_mirror_gates(conn, schema_name=schema, manifest=world.manifest, v1=gw.v1_scan(world), text=gw.text_scan(world))
    finally:
        await conn.close()
    assert gates.ok, gates.as_json()


def _by(rows, key: str) -> dict:
    return {row[key]: row for row in rows}


def test_reelshort_rows_keep_what_realshort_guarantees():
    """What the mirror's rows promise and the queries lean on (rs:src/lib/observe/queries.ts:463-466, :165-181;
    export-v2.ts:332-360): a verified yesterday or 7-day value comes with the raw snapshot it was read from, and only on
    a verified row; bill_orders and last_bill_on sum the canonical drama's bill rows; a bill for no drama had no click.
    One exception is kept on purpose, from gate_world: rr1 and p1 (rr7 and p7) are set independently there, so that each
    growth count differs from its neighbour; RealShort sets them together."""
    world = bf.board_world(bf.V2_TITLE, "no", bf.AS_OF["v2"])
    bills = world.tables["rs_bill_orders"]
    for row in world.tables["rs_rows"]:
        for day in ("1", "7"):
            verified = [row[f"rr{day}"], row[f"p{day}"]]
            raw = [row[f"s{day}_rr"], row[f"s{day}_p"]]
            assert (raw[0] is None) == (raw[1] is None), (row["drama_id"], day)
            if any(value is not None for value in verified):
                assert row["metrics_valid"] is True and raw[0] is not None, (row["drama_id"], day)
            assert row[f"rr{day}"] in (None, raw[0]) and row[f"p{day}"] in (None, raw[1]), (row["drama_id"], day)
        own = [bill for bill in bills if bill["canonical_id"] == row["drama_id"]]
        assert row["bill_orders"] == sum(bill["order_cnt"] for bill in own), row["drama_id"]
        assert row["last_bill_on"] == max((bill["bill_date"] for bill in own), default=None), row["drama_id"]
    canonical = {row["id"]: row["canonical_id"] for row in world.tables["rs_ids"]}
    for bill in bills:
        assert bill["canonical_id"] == canonical.get(bill["book_id"]), bill["book_id"]
        assert bill["canonical_id"] is not None or bill["same_day_clicks"] == 0, bill["book_id"]


def test_theater_rows_cover_the_rule_driven_filters():
    """Signaled, listed rows on a theater that only takes its YouTube list (one on the list, one not) and outside the
    theaters in use, so that yt=1 and inuse=1 each change what the board shows; every signaled row has a signal."""
    world = bf.board_world(bf.V2_TITLE, "no", bf.AS_OF["v2"])
    rules = world.manifest["meta"]["rules"]
    rows = _by(world.tables["catalog_rows"], "row_key")
    listed = [row for row in rows.values() if row["has_signal"] and row["off_on"] is None]
    list_only = [row for row in listed if rules["platformRules"][row["platform"]]["yt"] == "only"]
    assert sorted((row["row_key"], row["youtube"]) for row in list_only) == [("c-5", True), ("c-6", False)]
    assert all(row["platform"] not in rules["inUse"] for row in list_only)
    signaled = {signal["row_key"] for signal in world.tables["catalog_signals"]}
    assert {row["row_key"] for row in rows.values() if row["has_signal"]} <= signaled
    assert rows["c-3"]["in_site_ids"] == [bf.SIBLING[0]]


def test_down_refuses_names_it_did_not_make():
    assert bf.main(["down", "--database", "postgres", "--role", "pick_board_reader_000000000000"], out=io.StringIO()) == 2
    assert bf.main(["down", "--database", "pick_board_000000000000", "--role", "postgres"], out=io.StringIO()) == 2


def test_blobs_land_in_the_data_dir_itself_as_absolute_paths(pg_cluster, board):
    """The gateway roots PickService at its data dir (ggwork_pick/__init__.py): the blobs go straight under it."""
    info = board["info"]
    assert info["data_dir"] == str(board["data_dir"].resolve())
    paths = [row[0] for row in _rows(_admin_url(pg_cluster, info["database"]), "SELECT raw_blob_path FROM deerflow.ggwp_import_batches")]
    assert paths
    for path in map(Path, paths):
        assert path.is_absolute() and path.parent.parent == Path(info["data_dir"]) and path.is_file()


def test_the_data_dir_is_made_absolute(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert bf.gateway_data_dir(Path("gateway-data")) == tmp_path.resolve() / "gateway-data"
    made = bf.gateway_data_dir(None)
    try:
        assert made.is_absolute() and made.is_dir() and made.name.startswith("pick-board-")
    finally:
        made.rmdir()
