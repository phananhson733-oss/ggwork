"""2026-09-30: the data page shows four stale warnings (mirror 14 h, catalog 36 h, daily boards 2 d, weekly 14 d); the
agent had none, so a stopped sync or an upstream board that stopped updating read as the latest edition. The query
and count tools now add data_notices for the model, with the page's thresholds; the card and the stored snapshot are
unchanged."""

import json
import re
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from deerflow_extension_api import ExtensionData, TaskInfo
from deerflow_extension_api.runtime_bridge import EXTENSION_TASK_STORE_KEY
from engines import host_engine
from sqlalchemy.ext.asyncio import async_sessionmaker

FRONTEND = Path(__file__).resolve().parents[3] / "frontend/src"
BANNER_RULES_TS = FRONTEND / "components/workspace/pick-board/views/banner-rules.ts"
RANK_VIEW_TSX = FRONTEND / "components/workspace/pick-board/views/rank-view.tsx"
REQUEST_TS = FRONTEND / "core/pick-board/request.ts"

NOW = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)


def iso(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:00.000Z")


def as_of(*, captured: datetime = NOW - timedelta(hours=1), imported: datetime | None = None) -> dict:
    freshness = {"catalogImportedAt": iso(imported or captured), "reelshortSyncedAt": iso(captured)}
    return {"source_as_of": iso(captured), "published_at": captured.isoformat(), "freshness": freshness, "scope": "scope-a", "shared": True}


def signal(kind, observed, rank=1):
    return {"kind": kind, "source_ref": f"ref:{kind}:{observed}", "observed_at": observed, "rank": rank}


def row(i, *signals):
    base = {"identity": f"id{i}", "title": f"Fresh {i}", "theater": "KalosTV", "language": "en", "tags": [], "availability": "active"}
    return {**base, "channel_rules": {}, "signals": list(signals)}


def conditions(**fields):
    from ggwork_pick.contracts import PickConditions

    return PickConditions(**fields)


def notices(data_as_of, rows=(), **fields):
    from ggwork_pick.freshness import data_notices

    return data_notices(data_as_of, list(rows), conditions(**fields), now=NOW)


# ---- thresholds are the data page's ----------------------------------------------------------------------------


def test_thresholds_are_the_data_page_thresholds():
    from ggwork_pick import freshness
    from ggwork_pick.freshness import DAILY_RANKS, STALE_BATCH_HOURS, STALE_DAILY_RANK_DAYS, STALE_SOURCE_HOURS, STALE_WEEKLY_RANK_DAYS, WEEKLY_RANK

    banner = BANNER_RULES_TS.read_text(encoding="utf-8")
    stale = re.search(r"export const STALE_AFTER_MS = (\d+) \* 60 \* 60 \* 1000;", banner)
    assert stale, "STALE_AFTER_MS moved: keep the batch notice on the data page's stale threshold"
    assert int(stale.group(1)) == STALE_BATCH_HOURS
    source = re.search(r"reference - captured <= (\d+) \* 3600_000", banner)
    assert source, "sourceFreshnessBanners moved: keep the catalog notice on the data page's source threshold"
    assert int(source.group(1)) == STALE_SOURCE_HOURS
    rank = re.search(r'const days = board\.rank === "(\w+)" \? (\d+) : (\d+);', RANK_VIEW_TSX.read_text(encoding="utf-8"))
    assert rank, "TheaterRank's stale rule moved: keep the board notices on the rank tab's thresholds"
    assert (rank.group(1), int(rank.group(2)), int(rank.group(3))) == (WEEKLY_RANK, STALE_WEEKLY_RANK_DAYS, STALE_DAILY_RANK_DAYS)
    daily = re.search(r"export const DAILY_RANKS: ReadonlySet<string> = new Set<TheaterBasis>\(\[(.*?)\]\);", REQUEST_TS.read_text(encoding="utf-8"), re.S)
    assert daily, "DAILY_RANKS moved: keep the daily board kinds equal to the data page's"
    assert tuple(re.findall(r'"([a-z]+)"', daily.group(1))) == DAILY_RANKS
    assert freshness.RANK_KINDS == (*DAILY_RANKS, WEEKLY_RANK)


# ---- the pure function -----------------------------------------------------------------------------------------


def test_fresh_data_gets_no_notice():
    assert notices(as_of()) == []
    assert notices(as_of(), [row(1, signal("kd", "2026-09-30"))], signal_kind="kd") == []


def test_an_old_batch_warns_about_selected_data_without_claiming_sync_stopped():
    (notice,) = notices(as_of(captured=NOW - timedelta(hours=15)))
    assert "14 小时" in notice and "可能不是最新资料" in notice
    assert "同步可能停" not in notice and "回答里" not in notice
    assert "2026-09-29" in notice


def test_a_catalog_imported_more_than_thirty_six_hours_ago_says_signals_may_lag():
    (notice,) = notices(as_of(imported=NOW - timedelta(hours=37)))
    assert "36 小时" in notice and "剧单" in notice
    assert "只复制上游已有数据" in notice


def test_a_stale_daily_board_is_history_not_current_heat():
    rows = [row(1, signal("kd", "2026-09-27")), row(2, signal("kd", "2026-09-26"))]
    (notice,) = notices(as_of(), rows, signal_kind="kd")
    assert "kd" in notice and "2026-09-27" in notice and "2 天" in notice
    assert "历史" in notice and "不能" in notice


def test_a_weekly_board_allows_fourteen_days():
    rows = [row(1, signal("kw", "2026-09-20"))]
    assert notices(as_of(), rows, signal_kind="kw") == []
    rows = [row(1, signal("kw", "2026-09-10"))]
    (notice,) = notices(as_of(), rows, signal_kind="kw")
    assert "kw" in notice and "14 天" in notice


def test_board_age_is_measured_at_the_capture_like_the_rank_tab():
    # Captured two days ago with that day's board: the board was current when captured; the batch notice covers the rest.
    captured = NOW - timedelta(days=2)
    found = notices(as_of(captured=captured), [row(1, signal("kd", captured.strftime("%Y-%m-%d")))], signal_kind="kd")
    assert found and all("kd" not in notice for notice in found)


def test_hot_only_judges_each_board_kind_the_batch_holds():
    rows = [row(1, signal("kd", "2026-09-20")), row(2, signal("qc", "2026-09-30")), row(3, signal("sm", "2026-09-01"))]
    found = notices(as_of(), rows, hot_only=True)
    assert len(found) == 1 and "kd" in found[0]
    assert notices(as_of(), rows) == []


def test_missing_or_unreadable_fields_give_no_notice():
    assert notices(None) == []
    assert notices({"source_as_of": None, "published_at": None, "freshness": None, "scope": None, "shared": False}) == []
    assert notices({**as_of(), "source_as_of": "yesterday", "published_at": "soon", "freshness": {"catalogImportedAt": 3}}) == []
    assert notices(as_of(), [row(1, signal("kd", None))], signal_kind="kd") == []


# ---- through the tools -----------------------------------------------------------------------------------------


def catalog_row(i, observed):
    return {
        "source": "realshort-pick",
        "source_id": f"f{i}",
        "language": "en",
        "title": f"Fresh {i}",
        "theater": "KalosTV",
        "tags": [],
        "availability": "active",
        "channel_rules": {"youtube": "unknown"},
        "signals": [signal("kd", observed)],
        "posted": {"matched": False, "records": [], "post_count": 0, "sched_count": 0, "last_post_on": None, "accounts": []},
    }


@pytest.mark.asyncio
async def test_query_and_count_carry_data_notices_only_when_the_data_is_stale(tmp_path):
    from ggwork_pick.context import PickLifecycle
    from ggwork_pick.imports import Importer
    from ggwork_pick.repository import PickRepository
    from ggwork_pick.service import PickService
    from ggwork_pick.tools import count_candidates_tool, query_candidates_tool

    engine = host_engine(f"sqlite+aiosqlite:///{tmp_path / 'db'}")
    service = PickService(tmp_path / "files")
    await service.initialize(async_sessionmaker(engine, expire_on_commit=False))
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    stale_day = (datetime.now(UTC) - timedelta(days=5)).strftime("%Y-%m-%d")
    catalog = json.dumps([catalog_row(1, stale_day), catalog_row(2, stale_day)]).encode()
    await Importer(PickRepository(service.session_factory, "alice"), service.data_dir).catalog(catalog, "json")
    store = ExtensionData("task1")
    await PickLifecycle(service).on_task_start(ExtensionData("app"), store, TaskInfo("task1", "run1", "thread1", "lead"))
    runtime = SimpleNamespace(context={"user_id": "alice", EXTENSION_TASK_STORE_KEY: store}, tool_call_id="call1")
    queried = json.loads(await query_candidates_tool.coroutine(filters={"signal_kind": "kd"}, runtime=runtime))
    assert len(queried["items"]) == 2
    assert any("kd" in notice and stale_day in notice for notice in queried["data_notices"])
    assert set(queried["data_as_of"]) == {"source_as_of", "published_at", "freshness", "scope", "shared"}
    counted = json.loads(await count_candidates_tool.coroutine(filters={"signal_kind": "kd"}, runtime=runtime))
    assert counted["total"] == 2 and counted["data_notices"] == queried["data_notices"]
    second_call = SimpleNamespace(context=runtime.context, tool_call_id="call2")
    fresh = json.loads(await query_candidates_tool.coroutine(filters={"language": "en"}, runtime=second_call))
    assert "data_notices" not in fresh, "a just-published batch queried without a board kind has nothing to warn about"
    fresh_count = json.loads(await count_candidates_tool.coroutine(filters={"language": "en"}, runtime=runtime))
    assert "data_notices" not in fresh_count
    assert today != stale_day
    await engine.dispose()


def test_the_instructions_and_the_skill_tell_the_model_to_relay_data_notices():
    from ggwork_pick.middleware import PICK_INSTRUCTIONS

    assert "data_notices" in PICK_INSTRUCTIONS
    skill = (Path(__file__).resolve().parents[3] / "skills/public/pick-drama/SKILL.md").read_text(encoding="utf-8")
    assert "data_notices" in skill


@pytest.mark.parametrize("hours,field", [(14, "batch"), (36, "source")])
@pytest.mark.parametrize("extra", [timedelta(0), timedelta(microseconds=1)])
def test_hour_thresholds_keep_strict_greater_than_and_accept_offsets(hours, field, extra):
    moment = NOW - timedelta(hours=hours) - extra
    offset = moment.astimezone(timezone(timedelta(hours=8))).isoformat()
    data = as_of()
    if field == "batch":
        data["source_as_of"] = offset
    else:
        data["freshness"]["catalogImportedAt"] = offset
    found = notices(data)
    assert bool(found) == bool(extra)
    assert not found or f"{hours} 小时" in found[0]


@pytest.mark.parametrize("kind,days", [("kd", 2), ("qc", 2), ("qr", 2), ("kw", 14)])
@pytest.mark.parametrize("extra", [timedelta(0), timedelta(microseconds=1)])
def test_board_thresholds_use_capture_time_exactly(kind, days, extra):
    edition = datetime(2026, 9, 1, tzinfo=UTC)
    captured = edition + timedelta(days=days) + extra
    data = {**as_of(), "source_as_of": captured.isoformat()}
    found = notices(data, [row(1, signal(kind, "2026-09-01"))], signal_kind=kind)
    assert any(kind in notice for notice in found) == bool(extra)


def test_unknown_capture_does_not_fabricate_a_board_age():
    assert notices({"source_as_of": "broken", "published_at": None}, [row(1, signal("kd", "2020-01-01"))], signal_kind="kd") == []
