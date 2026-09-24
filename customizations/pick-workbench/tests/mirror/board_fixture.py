"""The database the pick data board reads in its integration tests and e2e (P3-1; U35, critique A1, A6).

Built through the production paths, in the gateway's own database: the gateway's startup migration, versions written by
create_version, copy_rows, write_meta and finalize_version, each published by PickRepository.publish_mirror_pair with a
staged v1 pair of the same moment (gate_world's v1 pull), and granted to a LOGIN reader set up the way
docs/pick-workbench/supabase/bootstrap.sql sets up pick_board_reader. The order is fixed: reader role, then migrate,
then publish. Migration 0006 and the publish both skip their grants silently when the role does not exist yet.

Versions, in id order:
  dropped   published, superseded, then dropped the way retention drops one
  v1        published, superseded
  v2        published and current: c-1 renamed, shortmax's platformRules yt from ok to no
  building  created, never finalized nor granted
  failed    created, then failed (its schema dropped)
plus pick_mirror.series for one canonical drama (rs0001) and series_state through / trimmed_before.

The ReelShort side is gate_world's, with its d-N ids renamed to ids shaped like RealShort's book ids (request.ts
reelshortId only takes [a-z0-9]{6,40}), and what the board's query tests (P3-3) need on top: metrics_valid of each kind,
a d1 tie that float8 arithmetic would break, publish dates for the buckets, exported bill ranks, one long tag list, a
non-canonical sibling (rs0006) and an id with no canonical row (rs0007), bill rows tied to their canonical id, a
theater row matched to rs0001, and signal payloads with distinct daily ranks and real week labels.

From customizations/pick-workbench, with the backend venv's python and PICK_TEST_PG_URL naming a throwaway cluster as
a superuser (the same variable the backend tests use):
  python tests/mirror/board_fixture.py up --url-file PATH [--data-dir DIR]
  python tests/mirror/board_fixture.py down --database NAME --role NAME
up prints the database, the role, the data directory and the version ids as JSON: names only. The data directory is
the gateway's own (its PickService root: PICK data_dir, or DEER_FLOW_HOME/pick): the staged batches' blobs are written
straight under it, and it is made absolute because raw_blob_path stores it. The reader's password is
PICK_BOARD_READER_PASSWORD when set, else generated; it is written only into --url-file (mode 0600), as the reader URL
for the frontend's PICK_BOARD_TEST_PG_URL. The role is created with a SCRAM verifier, so the password itself is never
sent as SQL. Every value is synthetic.
"""

import argparse
import asyncio
import json
import os
import re
import secrets
import sys
import tempfile
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import TextIO
from urllib.parse import quote

HERE = Path(__file__).resolve()
PASSWORD_ENV = "PICK_BOARD_READER_PASSWORD"
DATABASE_PREFIX = "pick_board"
ROLE_PREFIX = "pick_board_reader"
# unique_name's shape (tests/pg.py): down drops nothing else.
_DATABASE_NAME = re.compile(rf"{DATABASE_PREFIX}_[0-9a-f]{{12}}")
_ROLE_NAME = re.compile(rf"{ROLE_PREFIX}_[0-9a-f]{{12}}")
# bootstrap.sql's ALTER ROLE pick_board_reader SET ... lines, in order.
READER_SETTINGS = (
    ("search_path", "pick_mirror"),
    ("default_transaction_read_only", "on"),
    ("statement_timeout", "8s"),
    ("idle_in_transaction_session_timeout", "15s"),
    ("timezone", "UTC"),
)
# c-1 to c-4: shortmax is yt ok in v1 and no in v2; moboreels is no in both.
PLATFORMS = ("shortmax", "dramabox", "moboreels", "flareflow")
OLDEST_TITLE = "c-1 的剧名（最早）"
V1_TITLE = "c-1 的剧名"
V2_TITLE = "c-1 的剧名（v2 改名）"
LANGUAGE = "英语"
AS_OF = {
    "dropped": datetime(2026, 9, 22, 22, 15, tzinfo=UTC),
    "v1": datetime(2026, 9, 23, 10, 15, tzinfo=UTC),
    "v2": datetime(2026, 9, 23, 22, 15, tzinfo=UTC),
    "building": datetime(2026, 9, 24, 3, 38, tzinfo=UTC),
    "failed": datetime(2026, 9, 24, 3, 39, tzinfo=UTC),
}
SERIES_DAYS = (date(2026, 9, 21), date(2026, 9, 22), date(2026, 9, 23))
# gate_world's d-N as RealShort-shaped book ids; rs0006 is d-6, the sibling, and rs0007 has no canonical row at all.
BOOK_IDS = {f"d-{n}": f"rs{n:04d}" for n in range(1, 7)}
SIBLING = ("rs0006", "rs0001")
NO_CANONICAL = "rs0007"
SERIES_DRAMA = "rs0001"
# rs_rows columns on top of gate_world's, per book id; the publish dates are days before the version's as_of.
RS_EXTRA = {
    "rs0001": {"metrics_valid": True, "bill_rank": 2, "tag_list": [f"标签{n}" for n in range(1, 11)]},
    # rr − s1_rr ties at 1000.20 in numeric; in float8 1000.30 − 0.10 is 1000.1999999999999, so the tie breaks.
    "rs0002": {"metrics_valid": True, "rr": 1000.30, "s1_rr": 0.10},
    "rs0003": {"metrics_valid": True, "rr": 1000.20, "s1_rr": 0.00},
    "rs0004": {"metrics_valid": False, "bill_rank": 1},
}
PUBLISHED_DAYS_BEFORE = {"rs0001": 5, "rs0002": 20}
# catalog_signals payloads by gate_world's SIGNALS index: c-1's week list (kw), and kd ranks that differ on 2026-09-02.
SIGNAL_PAYLOADS = {
    1: {"h": [["2026-09-14", "9.14–9.20"], ["2026-09-07", "9.7–9.13"]], "weeks": 2},
    4: {"h": [["2026-09-02", 1, ""]]},
    5: {"h": [["2026-09-01", 2, ""]]},
}
SERIES_THROUGH = date(2026, 9, 23)
SERIES_TRIMMED_BEFORE = date(2026, 6, 25)
_MARK_DROPPED = "UPDATE pick_mirror.versions SET status = 'dropped', dropped_at = $2 WHERE id = $1 AND status = 'published'"
_SERIES = "INSERT INTO pick_mirror.series (drama_id, days, revenue_cents, promoters, updated_at) VALUES ($1, $2, $3, $4, $5)"
_SERIES_STATE = "UPDATE pick_mirror.series_state SET through = $1, trimmed_before = $2, updated_at = $3 WHERE id = 1"


def _import_paths() -> None:
    """What tests/conftest.py puts on sys.path, for a run from the command line."""
    for path in (HERE.parents[4] / "backend/packages/extension-api", HERE.parents[2], HERE.parents[1], HERE.parent):
        if str(path) not in sys.path:
            sys.path.insert(0, str(path))


def as_of_text(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:00.000Z")


# ---- the synthetic worlds ----------------------------------------------------------------------------------------


def _catalog_rows(world, title: str) -> list[dict]:
    """Real theaters and one language; c-2 is matched to rs0001 (in_site_ids), as a same-title match would be."""
    rows = zip(world.tables["catalog_rows"], PLATFORMS, strict=True)
    return [
        {
            **row,
            "platform": platform,
            "lang": LANGUAGE,
            "title": title if row["row_key"] == "c-1" else f"{row['row_key']} 的剧名",
            **({"in_site_ids": ["rs0001"]} if row["row_key"] == "c-2" else {}),
        }
        for row, platform in rows
    ]


def _book(drama_id: str | None) -> str | None:
    return BOOK_IDS.get(drama_id, drama_id) if drama_id is not None else None


def _rs_row(row: dict, as_of: datetime) -> dict:
    book = _book(row["drama_id"])
    days = PUBLISHED_DAYS_BEFORE.get(book)
    published = {"publish_at": as_of_text(as_of - timedelta(days=days))} if days is not None else {}
    names = {"row_key": f"reelshort-{book}", "drama_id": book, "title": f"{book} 的剧名", "slug": f"{book}-slug"}
    return {**row, **names, "platform": "reelshort", "lang": LANGUAGE, "locale": "en", **published, **RS_EXTRA.get(book, {})}


def _rs_id(row: dict, book: str, canonical: str | None) -> dict:
    public = canonical == book
    return {**row, "id": book, "canonical_id": canonical, "is_public_canonical": public, "locale": "en", "slug": f"{book}-slug", "title": f"{book} 的剧名"}


def _canonical_of(book: str) -> str:
    sibling, canonical = SIBLING
    return canonical if book == sibling else book


def _rs_ids(world) -> list[dict]:
    """Every book id its own canonical but the sibling, which points at rs0001, and one id with no canonical row."""
    books = [(row, _book(row["id"])) for row in world.tables["rs_ids"]]
    rows = [_rs_id(row, book, _canonical_of(book)) for row, book in books]
    return [*rows, _rs_id(world.tables["rs_ids"][-1], NO_CANONICAL, None)]


def _rs_tables(world, as_of: datetime) -> dict:
    """ReelShort rows with real-looking ids, locale and slug; bills, clicks and posted records follow the new ids."""
    bills = [{**row, "book_id": _book(row["book_id"]), "canonical_id": BOOK_IDS.get(row["book_id"])} for row in world.tables["rs_bill_orders"]]
    return {
        "rs_rows": [_rs_row(row, as_of) for row in world.tables["rs_rows"]],
        "rs_ids": _rs_ids(world),
        "rs_bill_orders": bills,
        "rs_clicks14": [{**row, "drama_id": _book(row["drama_id"])} for row in world.tables["rs_clicks14"]],
        "catalog_posted": [{**row, "drama_ids": [_book(i) for i in row["drama_ids"]]} for row in world.tables["catalog_posted"]],
        "catalog_signals": [{**row, "payload": SIGNAL_PAYLOADS.get(n, row["payload"])} for n, row in enumerate(world.tables["catalog_signals"])],
    }


def _v1_key(row_key: str) -> str:
    drama = row_key.removeprefix("reelshort-")
    return f"reelshort-{_book(drama)}" if drama != row_key else row_key


def _v1_rows(title: str) -> list[dict]:
    """gate_world's v1 candidates under the renamed ids: an identity names the row key the mirror has."""
    import gate_world as gw

    rows = [gw.v1_row(_v1_key(row_key), kinds, records) for row_key, (kinds, records) in gw.V1_CANDIDATES.items()]
    # The v1 pull of the same moment shows the same title; its rules page names the moment, so each pair is distinct.
    return [{**row, "title": title} if n == 0 else row for n, row in enumerate(rows)]


def board_world(title: str, shortmax_yt: str, as_of: datetime):
    """gate_world's baseline with real theaters, languages and book ids, one title and one YouTube rule chosen, at `as_of`."""
    import gate_world as gw

    world = gw.baseline()
    world = gw.with_table(world, "catalog_rows", _catalog_rows(world, title))
    for table, rows in _rs_tables(world, as_of).items():
        world = gw.with_table(world, table, rows)
    world = gw.with_counts(world, rs_ids=len(world.tables["rs_ids"]))
    world = gw.with_manifest(world, ("asOf",), as_of_text(as_of))
    world = gw.with_manifest(world, ("meta", "rules", "platformRules", "shortmax", "yt"), shortmax_yt)
    return gw.with_v1(world, _v1_rows(title))


def _rules_text(as_of: datetime) -> str:
    return f"# 选剧规则\n\n采集时间：{as_of_text(as_of)}\n\n只推荐带信号、未下架的剧。\n"


# ---- the versions ------------------------------------------------------------------------------------------------


async def _create(conn, world, as_of: datetime):
    from mirror_rows import version_args

    from ggwork_pick.mirror.versions import create_version

    manifest = world.manifest
    arguments = version_args(
        as_of=as_of,
        counts=manifest["counts"],
        latest_snapshot=manifest["latestSnapshot"],
        freshness=manifest["meta"]["freshness"],
        warnings=manifest["meta"]["warnings"],
        sync_run_id=f"board-{as_of_text(as_of)}",
        clock=lambda: as_of + timedelta(minutes=5),
    )
    return await create_version(conn, **arguments)


async def _build(conn, world, as_of: datetime):
    """A finalized version, written the way a run writes one (gate_world.build, at this world's as_of)."""
    from mirror_rows import TABLES, parsed

    from ggwork_pick.mirror.writer import copy_rows, finalize_version, write_meta

    version = await _create(conn, world, as_of)
    for table in TABLES:
        await copy_rows(conn, version.schema_name, table, parsed(table, list(world.tables[table])))
    await write_meta(conn, version.schema_name, world.manifest)
    await finalize_version(conn, version.schema_name)
    return version


async def _stage_v1(importer, world, as_of: datetime) -> list[dict]:
    """The run's v1 half: the pull's rows and its rules staged as a catalog and a knowledge batch (sync._pull)."""
    from mirror_pairs import RULES_REF

    text = as_of_text(as_of)
    meta = {"source": "realshort", "scope": "全部候选", "freshness": {}, "source_revision": "board-fixture", "source_total": len(world.v1_rows)}
    rows = json.dumps(list(world.v1_rows), ensure_ascii=False).encode("utf-8")
    catalog = await importer.catalog(rows, "json", source_as_of=text, meta=meta, keep_original=False, stage=True)
    rules = [(_rules_text(as_of).encode("utf-8"), "realshort-rules.md", RULES_REF)]
    knowledge = await importer.knowledge_bundle(rules, source_as_of=text, meta=meta, stage=True)
    return [catalog, knowledge]


async def _publish(conn, shared, importer, world, as_of: datetime):
    version = await _build(conn, world, as_of)
    batches = await _stage_v1(importer, world, as_of)
    moment = as_of + timedelta(minutes=30)
    await shared.publish_mirror_pair(
        version_id=version.id, schema_name=version.schema_name, batches=batches, t=moment, accept_empty_used=False, accept_empty_seen=None
    )
    return version


async def _drop_like_retention(conn, version, moment: datetime) -> None:
    from ggwork_pick.mirror.versions import drop_in_transaction

    async def mark() -> None:
        if await conn.execute(_MARK_DROPPED, version.id, moment) != "UPDATE 1":
            raise RuntimeError("the version to drop is no longer published")

    await drop_in_transaction(conn, version.schema_name, then=mark)


async def _versions(conn, shared, importer) -> dict[str, int]:
    from ggwork_pick.mirror.versions import mark_failed

    oldest = await _publish(conn, shared, importer, board_world(OLDEST_TITLE, "ok", AS_OF["dropped"]), AS_OF["dropped"])
    v1 = await _publish(conn, shared, importer, board_world(V1_TITLE, "ok", AS_OF["v1"]), AS_OF["v1"])
    v2 = await _publish(conn, shared, importer, board_world(V2_TITLE, "no", AS_OF["v2"]), AS_OF["v2"])
    await _drop_like_retention(conn, oldest, AS_OF["v2"] + timedelta(hours=2))
    building = await _create(conn, board_world(V2_TITLE, "no", AS_OF["building"]), AS_OF["building"])
    failed = await _create(conn, board_world(V2_TITLE, "no", AS_OF["failed"]), AS_OF["failed"])
    await mark_failed(conn, failed.id, error="board fixture: failed on purpose")
    return {"dropped": oldest.id, "v1": v1.id, "v2": v2.id, "building": building.id, "failed": failed.id}


async def _series(conn) -> None:
    moment = AS_OF["v2"] + timedelta(minutes=40)
    await conn.execute(_SERIES, SERIES_DRAMA, list(SERIES_DAYS), [1250.0, 980.5, 1430.25], [3, 2, 4], moment)
    await conn.execute(_SERIES_STATE, SERIES_THROUGH, SERIES_TRIMMED_BEFORE, moment)


# ---- the database and the reader ---------------------------------------------------------------------------------


@contextmanager
def _reader_role_env(role: str) -> Iterator[None]:
    """PICK_MIRROR_READER_ROLE for the migration and the publishes; the process environment is restored after."""
    from ggwork_pick.mirror.publish import READER_ROLE_ENV

    before = os.environ.get(READER_ROLE_ENV)
    os.environ[READER_ROLE_ENV] = role
    try:
        yield
    finally:
        if before is None:
            os.environ.pop(READER_ROLE_ENV, None)
        else:
            os.environ[READER_ROLE_ENV] = before


def create_reader(cluster, role: str, password: str) -> None:
    """A LOGIN reader with bootstrap.sql's settings; the password goes over the wire as a SCRAM verifier only."""
    import psycopg
    from psycopg import sql

    url = cluster.url.set(drivername="postgresql").render_as_string(hide_password=False)
    with psycopg.connect(url, autocommit=True) as conn:
        verifier = conn.pgconn.encrypt_password(password.encode(), role.encode(), b"scram-sha-256").decode()
        conn.execute(sql.SQL("CREATE ROLE {} LOGIN NOINHERIT PASSWORD {}").format(sql.Identifier(role), sql.Literal(verifier)))
        for name, value in READER_SETTINGS:
            conn.execute(sql.SQL("ALTER ROLE {} SET {} = {}").format(sql.Identifier(role), sql.Identifier(name), sql.Literal(value)))


def _grant_connect(cluster, database: str, role: str) -> None:
    from psycopg import sql

    cluster._execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(sql.Identifier(database), sql.Identifier(role)))


async def _open_gateway(url: str, data_dir: Path):
    """PickService rooted at data_dir itself, as the gateway roots it (ggwork_pick/__init__.py); mirror_pairs.open_service
    roots it at data_dir / "files" instead. Its initialize is the gateway's startup migration (tests/pg.py migrate)."""
    from engines import host_engine
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from ggwork_pick.imports import Importer
    from ggwork_pick.repository import PickRepository
    from ggwork_pick.service import PickService

    engine = host_engine(url)
    service = PickService(data_dir)
    await service.initialize(async_sessionmaker(engine, expire_on_commit=False))
    shared = PickRepository.shared(service.session_factory)
    return engine, shared, Importer(shared, service.data_dir)


def gateway_data_dir(arg: Path | None) -> Path:
    """--data-dir, or a new temporary directory, made absolute: raw_blob_path stores it, and the gateway reads those
    paths from its own working directory."""
    return (arg or Path(tempfile.mkdtemp(prefix="pick-board-"))).resolve()


async def build_board(cluster, database: str, role: str, data_dir: Path) -> dict[str, int]:
    """Migrate `database` and publish the versions into it; the reader role exists already."""
    from ggwork_pick.mirror.connection import dsn_from_url, open_dedicated

    url = cluster.async_url(database)
    with _reader_role_env(role):
        engine, shared, importer = await _open_gateway(url, data_dir)
        conn = await open_dedicated(dsn_from_url(url))
        try:
            ids = await _versions(conn, shared, importer)
            await _series(conn)
        finally:
            await conn.close()
            await engine.dispose()
    return ids


def reader_url(cluster, database: str, role: str, password: str) -> str:
    host = cluster.url.host or "127.0.0.1"
    port = cluster.url.port or 5432
    return f"postgresql://{role}:{quote(password, safe='')}@{host}:{port}/{database}"


def write_private(path: Path, text: str) -> None:
    """`text` into `path`, readable by its owner only, from the first byte on."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as file:
        os.fchmod(file.fileno(), 0o600)
        file.write(text)


def up(cluster, *, password: str, url_file: Path, data_dir: Path) -> dict:
    import pg

    database, role = pg.unique_name(DATABASE_PREFIX), pg.unique_name(ROLE_PREFIX)
    create_reader(cluster, role, password)
    try:
        cluster.create_database(database)
        _grant_connect(cluster, database, role)
        versions = asyncio.run(build_board(cluster, database, role, data_dir))
        write_private(url_file, reader_url(cluster, database, role, password))
    except BaseException:
        down(cluster, database=database, role=role)
        raise
    return {"database": database, "role": role, "data_dir": str(data_dir), "versions": versions}


def down(cluster, *, database: str, role: str) -> None:
    """The database first: it holds the grants to the role."""
    cluster.drop_database(database)
    cluster.drop_role(role)


# ---- the command line --------------------------------------------------------------------------------------------


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build or remove the pick data board's test database (synthetic data).")
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("up", help="create the reader role and the database, migrate, publish the versions")
    build.add_argument("--url-file", type=Path, required=True, help="where to write the reader URL (mode 0600)")
    build.add_argument("--data-dir", type=Path, help="the gateway's pick data directory, where the staged batches' blobs go (default: a new temporary one)")
    remove = commands.add_parser("down", help="drop a database and a reader role that up created")
    remove.add_argument("--database", required=True)
    remove.add_argument("--role", required=True)
    return parser


def main(argv: Sequence[str] | None = None, *, out: TextIO = sys.stdout) -> int:
    _import_paths()
    import pg

    args = _parser().parse_args(argv)
    admin = os.environ.get(pg.URL_ENV, "").strip()
    if not admin:
        print(f"{pg.URL_ENV} is not set: name a throwaway cluster, as a superuser", file=sys.stderr)
        return 2
    cluster = pg.PgCluster(admin)
    if args.command == "down":
        if not (_DATABASE_NAME.fullmatch(args.database) and _ROLE_NAME.fullmatch(args.role)):
            print("down only drops a database and a role that up created", file=sys.stderr)
            return 2
        down(cluster, database=args.database, role=args.role)
        return 0
    password = os.environ.get(PASSWORD_ENV) or secrets.token_urlsafe(24)
    data_dir = gateway_data_dir(args.data_dir)
    print(json.dumps(up(cluster, password=password, url_file=args.url_file, data_dir=data_dir), ensure_ascii=False), file=out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
