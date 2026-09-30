"""The simplified radar's task source (simplified scope 2026-09-30, sections 3 and 6 item 1): who is in each night, in
which order, with which basis, and the unit each one gets. The pure parts run anywhere; the catalog read runs on both
dialects; the ReelShort revenue fill on PostgreSQL only (SQLite has no mirror)."""

import json
from datetime import UTC, date, datetime

import pytest
import pytest_asyncio
from obs_db_helpers import is_postgres, migrated
from top_dramas_fixtures import BOARD_ISSUE, board_drama, board_identity, seed_mirror
from trends_session_helpers import TARGET, seed_catalog

from ggwork_pick.observe.errors import Refused
from ggwork_pick.observe.lease import status_reader
from ggwork_pick.observe.trends import top_dramas as top
from ggwork_pick.observe.trends.top_dramas import Basis, Candidate, CatalogEntry, Signal, board, merged, search_term

# ---- the search term ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("title", "term"),
    [
        ("The Alpha's Bride (Dubbed)", "The Alpha's Bride"),
        ("The Alpha’s Bride [English Dub]", "The Alpha's Bride"),
        ("Mr. & Mrs. Smith!", "Mr Mrs Smith"),
        ("Love, Lies & Revenge: Part 2", "Love Lies Revenge Part 2"),
        ("Is It Just Me?", "Is It Just Me"),
        ("“Quoted” Title", "Quoted Title"),
        ("La Esposa del CEO (Doblado)", "La Esposa del CEO"),
        ("A Noiva do Alfa - Dublado", "A Noiva do Alfa"),
        ("La Mariée (Doublé)", "La Mariée"),
        ("Die Braut (Synchronisiert)", "Die Braut"),
        ("La sposa (doppiato)", "La sposa"),
        ("The Heiress Dubbed", "The Heiress"),
        ("Title - English Dub (Dubbed)", "Title"),
        ("【配音版】霸道总裁爱上我", "霸道总裁爱上我"),
        ("霸道总裁爱上我（配音）", "霸道总裁爱上我"),
        ("Dubai Billionaire's Bride", "Dubai Billionaire's Bride"),
        ("Dubbing Queen's Revenge", "Dubbing Queen's Revenge"),
        ("  Too   many   spaces  ", "Too many spaces"),
        ("Ｆｕｌｌｗｉｄｔｈ Title", "Fullwidth Title"),
        ("Rock 'n' Roll", "Rock n Roll"),
    ],
)
def test_search_term_cleans_punctuation_and_dub_markers(title, term):
    assert search_term(title) == term


@pytest.mark.parametrize("title", ["", "   ", "!!!", "(Dubbed)", "x" * 201, None])
def test_search_term_without_anything_usable_is_none(title):
    assert search_term(title) is None


# ---- a board --------------------------------------------------------------------------------------------------------

EARLIER = date(2026, 9, 20)


def _entry(name: str, *signals: Signal, language: str = "en") -> CatalogEntry:
    return CatalogEntry(f"id-{name}", f"Title {name}", "DramaBox", language, tuple(signals))


def test_a_board_is_its_latest_issue_by_rank():
    """The latest issue any drama holds a rank of the kind; a drama's older rank, another kind's rank, and dramas only
    on older issues do not count; a drama twice on the issue keeps its best rank; ties go by identity."""
    entries = [
        _entry("a", Signal("qc", BOARD_ISSUE, 3), Signal("qc", EARLIER, 1)),
        _entry("b", Signal("qc", BOARD_ISSUE, 1), Signal("qr", BOARD_ISSUE, 9)),
        _entry("c", Signal("qc", EARLIER, 2)),
        _entry("d", Signal("qc", BOARD_ISSUE, 5), Signal("qc", BOARD_ISSUE, 2)),
        _entry("e", Signal("qc", BOARD_ISSUE, 3)),
    ]
    found = board(entries, "qc")
    assert found.issue == BOARD_ISSUE
    assert [(c.identity, c.basis.rank) for c in found.candidates] == [("id-b", 1), ("id-d", 2), ("id-a", 3), ("id-e", 3)]
    assert {c.basis for c in found.candidates} >= {Basis("qc", BOARD_ISSUE, 1)}
    assert found.note() == {"kind": "qc", "board_date": "2026-09-24", "listed": 4}


def test_an_empty_board_has_no_issue():
    found = board([_entry("a", Signal("qr", BOARD_ISSUE, 1))], "kd")
    assert (found.issue, found.candidates, found.note()) == (None, (), {"kind": "kd", "board_date": None, "listed": 0})


def test_only_ranked_board_signals_are_read():
    payload = board_drama(
        1,
        title="X",
        boards={"qc": 4},
        extra_signals=[
            {"kind": "qr", "source_ref": "r", "observed_at": "2026-09-24", "rank": None},
            {"kind": "kw", "source_ref": "w", "observed_at": "2026-09-24", "rank": 1},
            {"kind": "kd", "source_ref": "k", "observed_at": "not a day", "rank": 2},
            {"kind": "kd", "source_ref": "k", "observed_at": "2026-09-24T08:00:00+00:00", "rank": 7},
        ],
    )
    entry = top.catalog_entry("id", payload)
    assert entry.signals == (Signal("qc", BOARD_ISSUE, 4), Signal("kd", BOARD_ISSUE, 7))
    assert (entry.platform, entry.language) == ("DramaBox", "en")
    assert top.catalog_entry("id", {"title": 3, "language": "en"}) is None


# ---- merging the candidates -----------------------------------------------------------------------------------------


def _candidate(identity: str, title: str, kind: str = "qc", rank: int = 1) -> Candidate:
    return Candidate(identity, title, "DramaBox", "en", Basis(kind, BOARD_ISSUE, rank))


def test_merged_keeps_order_and_joins_the_same_identity_or_term():
    picks, unusable = merged(
        [
            _candidate("a", "Alpha Bride", "qc", 1),
            _candidate("b", "Second One", "qc", 2),
            _candidate("a", "Alpha Bride", "qr", 5),  # the same identity on another board
            _candidate("c", "alpha bride!", "kd", 1),  # another drama asking Google the same term
            _candidate("d", "???", "kd", 2),
            _candidate("e", "Third", "kd", 3),
        ],
        target=3,
    )
    assert [pick.identity for pick in picks] == ["a", "b", "e"] and unusable == 1
    first = picks[0]
    assert first.term == "Alpha Bride"
    assert first.basis == (Basis("qc", BOARD_ISSUE, 1), Basis("qr", BOARD_ISSUE, 5), Basis("kd", BOARD_ISSUE, 1, identity="c"))


def test_merged_stops_at_the_target_but_still_joins_the_basis():
    picks, _ = merged([_candidate("a", "One"), _candidate("b", "Two"), _candidate("c", "Three"), _candidate("a", "One", "revenue", 9)], target=2)
    assert [pick.identity for pick in picks] == ["a", "b"]
    assert picks[0].basis == (Basis("qc", BOARD_ISSUE, 1), Basis("revenue", BOARD_ISSUE, 9))


def test_a_pick_note_and_its_unit():
    picks, _ = merged([_candidate("a", "Alpha Bride (Dubbed)")])
    note = picks[0].to_note(1)
    assert note == {
        "order": 1,
        "identity": "a",
        "title": "Alpha Bride (Dubbed)",
        "platform": "DramaBox",
        "language": "en",
        "basis": [{"kind": "qc", "board_date": "2026-09-24", "rank": 1}],
    }
    unit = top.query_unit(picks[0], 7)
    assert (unit.item, unit.geo, unit.granularity, unit.terms, unit.bare, unit.priority) == ("title", "WW", "D", ("Alpha Bride",), "Alpha Bride", 7)
    assert (unit.related, unit.timeline, unit.http, unit.identity) == (False, True, 2, "a")
    assert unit.query().timeframe == "today 1-m" and unit.query().google_geo == ""


def test_rs_identity_is_the_catalogs_spelling_of_a_reelshort_row():
    """identity_row_key_cases.json: source realshort-pick, source_id the row key in unpadded base64url."""
    assert json.loads(top.rs_identity("abc123", "en")) == ["realshort-pick", "cmVlbHNob3J0LWFiYzEyMw", "en"]


def test_snapshot_day_is_the_latest_snapshot_no_later_than_as_of():
    as_of = datetime(2026, 9, 29, 23, 30, tzinfo=UTC)
    assert top._snapshot_day(as_of, date(2026, 9, 28)) == date(2026, 9, 28)
    assert top._snapshot_day(as_of, date(2026, 9, 30)) == date(2026, 9, 29)
    assert top._snapshot_day(as_of, None) == date(2026, 9, 29)


# ---- the source on a database ---------------------------------------------------------------------------------------


@pytest_asyncio.fixture
async def obs_url(pick_db_url, tmp_path):
    return await migrated(pick_db_url, tmp_path)


async def _units(url: str, env: dict | None = None, *, target: int = top.TARGET_DRAMAS):
    async with status_reader("trends", environ=env or _env(url)) as step:
        return await top.TopDramasTaskSource(target=target).units(step, target_date=TARGET)


def _env(url: str) -> dict[str, str]:
    from obs_db_helpers import collector_env

    return collector_env(url)


BOARD_CATALOG = [
    board_drama(1, title="Kalos Hit", boards={"kd": 1}, theater="KalosTV"),
    board_drama(2, title="Conversion Two", boards={"qc": 2}),
    board_drama(3, title="Conversion One", boards={"qc": 1, "qr": 3}),
    board_drama(4, title="Revenue One", boards={"qr": 1}),
    board_drama(5, title="conversion one!", boards={"kd": 2}, theater="KalosTV"),  # the same term as drama 3
    board_drama(6, title="Old Issue", boards={"qc": 1}, issue=date(2026, 9, 1)),
    board_drama(7, title="No Board"),
]


@pytest.mark.asyncio
async def test_boards_in_their_order_each_by_rank(obs_url):
    """qc, then qr, then kd, each on its latest issue by its own rank; a drama on two boards and a drama asking the same
    term take one unit, the other bases joined; the notes keep every pick's basis for the table."""
    batch = await seed_catalog(obs_url, BOARD_CATALOG)
    found = await _units(obs_url)
    assert found.catalog_batch_id == batch
    picks = found.notes["top_dramas"]["picks"]
    ordered = [picks[unit.key] for unit in found.units]
    assert [(pick["order"], pick["title"]) for pick in ordered] == [(1, "Conversion One"), (2, "Conversion Two"), (3, "Revenue One"), (4, "Kalos Hit")]
    assert ordered[0]["basis"] == [
        {"kind": "qc", "board_date": "2026-09-24", "rank": 1},
        {"kind": "qr", "board_date": "2026-09-24", "rank": 3},
        {"kind": "kd", "board_date": "2026-09-24", "rank": 2, "identity": board_identity(5)},
    ]
    assert ordered[3]["platform"] == "KalosTV" and ordered[0]["identity"] == board_identity(3)
    assert [unit.priority for unit in found.units] == [1, 2, 3, 4]
    notes = found.notes["top_dramas"]
    assert notes["boards"] == [
        {"kind": "qc", "board_date": "2026-09-24", "listed": 2},
        {"kind": "qr", "board_date": "2026-09-24", "listed": 2},
        {"kind": "kd", "board_date": "2026-09-24", "listed": 2},
    ]
    assert (notes["target"], notes["picked"], notes["unusable_titles"], found.notes["catalog_dramas"]) == (100, 4, 0, 7)
    expected = "sqlite" if not is_postgres(obs_url) else "no_version"
    assert notes["revenue"] == {"available": False, "reason": expected, "mirror_version": None, "day": None, "filled": 0}


@pytest.mark.asyncio
async def test_boards_alone_can_fill_the_target(obs_url):
    await seed_catalog(obs_url, BOARD_CATALOG)
    found = await _units(obs_url, target=2)
    assert [found.notes["top_dramas"]["picks"][unit.key]["title"] for unit in found.units] == ["Conversion One", "Conversion Two"]
    assert found.notes["top_dramas"]["revenue"]["reason"] == "not_needed"


@pytest.mark.asyncio
async def test_nothing_to_pick_is_refused(obs_url):
    """No board drama and no revenue to fill with (SQLite has no mirror; the PostgreSQL copy has no version): the night
    would measure nothing, so it is refused before any request."""
    with pytest.raises(Refused):  # no shared catalog batch at all
        await _units(obs_url)
    await seed_catalog(obs_url, [board_drama(7, title="No Board")])
    with pytest.raises(Refused):
        await _units(obs_url)


# ---- the ReelShort revenue fill (PostgreSQL) -------------------------------------------------------------------------

AS_OF = datetime(2026, 9, 29, 3, 0, tzinfo=UTC)
DAY = date(2026, 9, 28)
MIRROR = [
    {"id": "rs-1", "title": "RS Top", "series": [(date(2026, 9, 27), 900), (DAY, 5000)]},
    {"id": "rs-2", "title": "RS Second", "locale": "ES", "series": [(DAY, 4000)]},
    {"id": "rs-3", "title": "Conversion One", "series": [(DAY, 3000)]},  # the same term as a board drama
    {"id": "rs-4", "title": "RS Stale", "series": [(date(2026, 9, 27), 9999)]},  # no point on the snapshot day
    {"id": "rs-5", "title": "RS Zero", "series": [(DAY, 0)]},
    {"id": "rs-6", "title": "RS Sibling", "canonical": "rs-1", "series": [(DAY, 8000)]},  # not canonical
    {"id": "rs-7", "title": "RS Third", "series": [(DAY, 2000), (date(2026, 9, 29), 7000)]},  # a day after the snapshot
]


@pytest.mark.asyncio
async def test_revenue_fills_after_the_boards(obs_url):
    """Short of the target, ReelShort's canonical dramas by revenue on the version's snapshot day (latest_snapshot, not
    as_of's later day): ranks count every canonical drama with revenue that day; one without a point that day, with
    zero, or not canonical is not ranked; a title a board drama already asks joins its basis."""
    if not is_postgres(obs_url):
        pytest.skip("the mirror is PostgreSQL's")
    await seed_catalog(obs_url, BOARD_CATALOG[:3])
    await seed_mirror(obs_url, MIRROR, as_of=AS_OF, latest_snapshot=DAY)
    found = await _units(obs_url)
    picks = [found.notes["top_dramas"]["picks"][unit.key] for unit in found.units]
    assert [(pick["title"], pick["platform"], pick["language"]) for pick in picks] == [
        ("Conversion One", "DramaBox", "en"),
        ("Conversion Two", "DramaBox", "en"),
        ("Kalos Hit", "KalosTV", "en"),
        ("RS Top", "ReelShort", "en"),
        ("RS Second", "ReelShort", "es"),
        ("RS Third", "ReelShort", "en"),
    ]
    assert picks[3]["basis"] == [{"kind": "revenue", "board_date": "2026-09-28", "rank": 1}]
    assert picks[5]["basis"] == [{"kind": "revenue", "board_date": "2026-09-28", "rank": 4}]
    assert picks[0]["basis"][-1] == {"kind": "revenue", "board_date": "2026-09-28", "rank": 3, "identity": top.rs_identity("rs-3", "en")}
    assert picks[3]["identity"] == top.rs_identity("rs-1", "en")
    assert found.notes["top_dramas"]["revenue"] == {"available": True, "reason": None, "mirror_version": 1, "day": "2026-09-28", "filled": 3}


@pytest.mark.asyncio
async def test_revenue_without_a_readable_version_leaves_the_boards_alone(obs_url):
    if not is_postgres(obs_url):
        pytest.skip("the mirror is PostgreSQL's")
    await seed_catalog(obs_url, BOARD_CATALOG[:2])
    schema = await seed_mirror(obs_url, MIRROR[:1], as_of=AS_OF, latest_snapshot=DAY)
    from obs_db_helpers import execute

    await execute(obs_url, f"drop table {schema}.rs_ids")
    found = await _units(obs_url)
    assert len(found.units) == 2
    assert found.notes["top_dramas"]["revenue"] == {"available": False, "reason": "unreadable", "mirror_version": 1, "day": None, "filled": 0}
