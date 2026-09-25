"""The canary's task source (plan TR-14, section 9; design 4.5, 4.11) and the control list it reads.

The control list is TR-05's trends/canary_controls.json (format trends-canary-controls-v1): identity keys, geo and
group for each control drama, and one market phrase per geo, never a title. Titles come from the current shared
catalog batch at run time, read through the collector's step (ggwp_import_batches and ggwp_drama_versions, which the
observer may only read). Without the file the canary does not run: exit 2, before any request; nor without a published
shared catalog batch (no titles, no controls: the market series alone measure nothing the canary is for).

The canary's units, each one term (the bare title, design 4.7's judgement line) in a request of its own:
- the market series: one per geo in the list (design 4.7's control series), priority 1. The list must have one for
  every geo the canary queries (every control's geo, and every first-round geo of the six languages), or the canary
  refuses to run (exit 2, naming the missing geos): design 4.5 counts one market unit per geo, and TR-17 and TR-30
  read each geo against its own. Checked when the source is built, before the database, so the load never changes
  in the middle of the canary when a title in a new language turns up;
- the control dramas at their geo, priority 2;
- the current batch's dramas in the six Euro-American languages listed within 14 days of the target date (listed_at
  from target-14 to the target date, both ends in: ages 0 to 14, the span design 4.6's rule 2 splits into 0-7 and
  8-14), at the geos market-map-v1 plans for their language: first-round A geos at priority 3, B geos at priority 4;
- relatedsearches on every RELATED_EVERY-th drama unit in truncation order, unless the route is a_only (section 8).
Only the titles are the day's own; the parameters stay fixed for the whole canary, so its days compare (section 9).
"""

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import select

from ggwork_pick.models import drama_versions, import_batches
from ggwork_pick.observe.contract import TREND_GEO_PATTERN
from ggwork_pick.observe.errors import Refused
from ggwork_pick.observe.lease import ReadStep
from ggwork_pick.observe.market_map import MARKET_MAP_V1, MarketMap
from ggwork_pick.observe.trends.source import MAX_TERM_LENGTH
from ggwork_pick.observe.trends.units import QueryUnit, ordered, unit_key

CONTROLS_FORMAT = "trends-canary-controls-v1"
DEFAULT_CONTROLS_PATH = Path(__file__).with_name("canary_controls.json")
SOURCE_NAME = "canary"
SHARED_OWNER = "system:shared"  # repository.SHARED_OWNER; the collector never imports the gateway's repository
CATALOG_KIND = "catalog"
RECENT_DAYS = 14
RELATED_EVERY = 4  # about a tenth of the day's requests, design 4.5's "歧义相关查询" share
PRIORITY_MARKET, PRIORITY_CONTROL, PRIORITY_FIRST_ROUND_A, PRIORITY_FIRST_ROUND_B = 1, 2, 3, 4
MAX_CONTROLS_BYTES = 1_000_000
_CONTROL_KEYS = frozenset({"identity", "geo", "group"})
_MARKET_KEYS = frozenset({"geo", "term"})


@dataclass(frozen=True)
class CanaryControl:
    identity: str
    geo: str
    group: str


@dataclass(frozen=True)
class MarketSeries:
    geo: str
    term: str


@dataclass(frozen=True)
class CanaryControls:
    controls: tuple[CanaryControl, ...]
    market: tuple[MarketSeries, ...]


def _geo_ok(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(TREND_GEO_PATTERN, value) is not None


def _text_ok(value: object, limit: int) -> bool:
    return isinstance(value, str) and 0 < len(value.strip()) <= limit and value.isprintable()


def _identity_ok(value: object) -> bool:
    try:
        parts = json.loads(value) if isinstance(value, str) else None
    except ValueError:
        return False
    return isinstance(parts, list) and len(parts) == 3 and all(isinstance(part, str) and part for part in parts)


def _control_problem(index: int, entry: object) -> str | None:
    if not isinstance(entry, Mapping) or set(entry) != _CONTROL_KEYS:
        return f"controls[{index}] 须恰好有 identity、geo、group"
    if not _identity_ok(entry["identity"]):
        return f"controls[{index}].identity 须是 [source, source_id, language] 的 JSON 文本"
    if not _geo_ok(entry["geo"]) or not _text_ok(entry["group"], 40):
        return f"controls[{index}] 的 geo 须是 WW 或两位国家码，group 须是非空短文本"
    return None


def _market_problem(index: int, entry: object) -> str | None:
    if not isinstance(entry, Mapping) or set(entry) != _MARKET_KEYS:
        return f"market[{index}] 须恰好有 geo、term"
    if not _geo_ok(entry["geo"]) or not _text_ok(entry["term"], MAX_TERM_LENGTH):
        return f"market[{index}] 的 geo 须是 WW 或两位国家码，term 须是 1–{MAX_TERM_LENGTH} 个可打印字符"
    return None


def parse_controls(document: object) -> CanaryControls:
    """The control list, or ValueError naming every problem (never a value from the file)."""
    if not isinstance(document, Mapping) or document.get("format") != CONTROLS_FORMAT:
        raise ValueError(f"对照清单的 format 须是 {CONTROLS_FORMAT}")
    controls, market = document.get("controls"), document.get("market")
    if not isinstance(controls, list) or not isinstance(market, list):
        raise ValueError("对照清单须有 controls 与 market 两个列表")
    problems = [problem for index, entry in enumerate(controls) if (problem := _control_problem(index, entry))]
    problems += [problem for index, entry in enumerate(market) if (problem := _market_problem(index, entry))]
    if problems:
        raise ValueError("对照清单不合格：" + "；".join(problems))
    pairs = [(entry["identity"], entry["geo"]) for entry in controls]
    geos = [entry["geo"] for entry in market]
    if len(set(pairs)) != len(pairs) or len(set(geos)) != len(geos):
        raise ValueError("对照清单不合格：同一 identity 与 geo 的对照、或同一 geo 的市场序列重复")
    return CanaryControls(
        tuple(CanaryControl(entry["identity"], entry["geo"], entry["group"].strip()) for entry in controls),
        tuple(MarketSeries(entry["geo"], entry["term"].strip()) for entry in market),
    )


def load_controls(path: Path | None = None) -> CanaryControls:
    """The canary's control list; Refused (exit 2) when it is missing or malformed: the canary does not run without it.
    The messages name the file and the problem, never a value from it."""
    target = DEFAULT_CONTROLS_PATH if path is None else Path(path)
    if not target.is_file():
        raise Refused(f"没有金丝雀对照清单 {target.name}（TR-05 阶段 0 的产物，放在包内 trends/ 下）：拒绝跑金丝雀")
    try:
        raw = target.read_bytes()
    except OSError as exc:
        raise Refused(f"金丝雀对照清单 {target.name} 读不了（{type(exc).__name__}）：拒绝跑金丝雀") from None
    if len(raw) > MAX_CONTROLS_BYTES:
        raise Refused(f"金丝雀对照清单 {target.name} 超过 {MAX_CONTROLS_BYTES} 字节：拒绝跑金丝雀")
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise Refused(f"金丝雀对照清单 {target.name} 不是 UTF-8 JSON：拒绝跑金丝雀") from None
    try:
        return parse_controls(document)
    except ValueError as exc:
        raise Refused(f"金丝雀对照清单 {target.name} 不合格式（{exc}）：拒绝跑金丝雀") from None


# ---- the shared catalog, read through the collector's step --------------------------------------------------------


@dataclass(frozen=True)
class CatalogDrama:
    identity: str
    title: str
    language: str
    listed_at: date | None
    evidence_on: date | None  # the latest signal date: design 4.5's third tie-break


def _day(value: object) -> date | None:
    """A catalog date (listed_at, a signal's observed_at): YYYY-MM-DD or an ISO time; anything else is no date."""
    if not isinstance(value, str) or len(value) < 10:
        return None
    try:
        return datetime.fromisoformat(value).date() if len(value) > 10 else date.fromisoformat(value)
    except ValueError:
        return None


def _drama(identity: str, payload: object) -> CatalogDrama | None:
    if not isinstance(payload, Mapping) or not isinstance(payload.get("title"), str) or not isinstance(payload.get("language"), str):
        return None
    signals = payload.get("signals") if isinstance(payload.get("signals"), list) else []
    seen = [day for signal in signals if isinstance(signal, Mapping) and (day := _day(signal.get("observed_at"))) is not None]
    return CatalogDrama(identity, payload["title"].strip(), payload["language"].strip().lower(), _day(payload.get("listed_at")), max(seen, default=None))


async def current_catalog(step: ReadStep) -> tuple[str | None, tuple[CatalogDrama, ...]]:
    """The current shared catalog batch (the newest published one, as repository.current_batch picks it) and its
    dramas; (None, ()) when there is none yet."""
    latest = (
        select(import_batches.c.id)
        .where(import_batches.c.owner_id == SHARED_OWNER, import_batches.c.kind == CATALOG_KIND, import_batches.c.status == "published")
        .order_by(import_batches.c.published_at.desc(), import_batches.c.id.desc())
        .limit(1)
    )
    batch_id = (await step.execute(latest)).scalar()
    if batch_id is None:
        return None, ()
    found = await step.execute(select(drama_versions.c.identity, drama_versions.c.payload_json).where(drama_versions.c.batch_id == batch_id))
    parsed = (_drama(identity, _json(payload)) for identity, payload in found.all())
    return batch_id, tuple(drama for drama in parsed if drama is not None)


def _json(value: object) -> object:
    return json.loads(value) if isinstance(value, str) else value


# ---- the canary's units ---------------------------------------------------------------------------------------------


def euro_american_geos(market_map: MarketMap = MARKET_MAP_V1) -> Mapping[str, tuple[tuple[str, int], ...]]:
    """Language -> (geo, priority) for the six Euro-American languages: the geos market-map-v1 plans for the first round
    (A before B); a geo with no first-round tier (GB, BG) is not a canary geo."""
    tiers = {"A": PRIORITY_FIRST_ROUND_A, "B": PRIORITY_FIRST_ROUND_B}
    found: dict[str, tuple[tuple[str, int], ...]] = {}
    for plan in market_map.geo_plans:
        if plan.first_round in tiers:
            for language in plan.languages:
                found = {**found, language: (*found.get(language, ()), (plan.geo, tiers[plan.first_round]))}
    return found


def _usable(title: str) -> bool:
    return 0 < len(title) <= MAX_TERM_LENGTH and title.isprintable()


def _drama_unit(drama: CatalogDrama, geo: str, granularity: str, priority: int, *, item: str, group: str | None = None) -> QueryUnit:
    return QueryUnit(
        key=unit_key("drama", drama.identity, geo, granularity),
        item=item,
        geo=geo,
        terms=(drama.title,),
        bare=drama.title,
        granularity=granularity,
        priority=priority,
        identity=drama.identity,
        listed_at=drama.listed_at,
        evidence_on=drama.evidence_on,
        group=group,
    )


@dataclass(frozen=True)
class SourceUnits:
    units: tuple[QueryUnit, ...]
    catalog_batch_id: str | None
    notes: Mapping[str, Any]


def missing_market_geos(controls: CanaryControls, market_map: MarketMap = MARKET_MAP_V1) -> tuple[str, ...]:
    """The geos the canary queries (its controls' and the six languages' first-round geos) that have no market series
    in the list, sorted."""
    queried = {control.geo for control in controls.controls} | {geo for pairs in euro_american_geos(market_map).values() for geo, _ in pairs}
    return tuple(sorted(queried - {series.geo for series in controls.market}))


class CanaryTaskSource:
    """The canary's units for a target date (section 9: fixed parameters, fresh titles)."""

    name = SOURCE_NAME

    def __init__(self, controls: CanaryControls, *, granularities: Sequence[str], related: bool, market_map: MarketMap = MARKET_MAP_V1):
        missing = missing_market_geos(controls, market_map)
        if missing:
            raise Refused(f"金丝雀对照清单缺这些 geo 的市场对照序列：{'、'.join(missing)}（设计 4.7：每个查询的 geo 一条，由 TR-05 补短语）：拒绝跑金丝雀")
        self._controls, self._granularities, self._related, self._market_map = controls, tuple(granularities), related, market_map

    async def units(self, step: ReadStep, *, target_date: date) -> SourceUnits:
        """The day's units in truncation order, relatedsearches mixed in; a control whose identity the current batch
        does not hold is left out and named in the notes."""
        batch_id, dramas = await current_catalog(step)
        if batch_id is None:
            raise Refused("没有已发布的共享剧库批次：金丝雀没有剧名与对照剧可查，只剩市场序列，拒绝跑金丝雀")
        by_identity = {drama.identity: drama for drama in dramas if _usable(drama.title)}
        market = tuple(self._market_units())
        controls, missing = self._control_units(by_identity)
        recent = tuple(self._recent_units(by_identity.values(), target_date))
        units = with_related(ordered((*market, *controls, *recent), target_date), related=self._related)
        notes = {"missing_controls": list(missing), "catalog_dramas": len(dramas), "recent_units": len(recent)}
        return SourceUnits(units, batch_id, notes)

    def _market_units(self):
        for series in self._controls.market:
            for granularity in self._granularities:
                key = unit_key("market", series.term, series.geo, granularity)
                yield QueryUnit(key, "market", series.geo, (series.term,), series.term, granularity, PRIORITY_MARKET)

    def _control_units(self, by_identity: Mapping[str, CatalogDrama]) -> tuple[tuple[QueryUnit, ...], tuple[str, ...]]:
        """Controls missing from the current batch are named (their identity keys) and left out."""
        missing = tuple(control.identity for control in self._controls.controls if control.identity not in by_identity)
        units = tuple(
            _drama_unit(by_identity[control.identity], control.geo, granularity, PRIORITY_CONTROL, item="control", group=control.group)
            for control in self._controls.controls
            if control.identity in by_identity
            for granularity in self._granularities
        )
        return units, missing

    def _recent_units(self, dramas, target_date: date):
        first = target_date - timedelta(days=RECENT_DAYS)
        geos = euro_american_geos(self._market_map)
        for drama in dramas:
            if drama.listed_at is None or not first <= drama.listed_at <= target_date:
                continue
            for geo, priority in geos.get(drama.language, ()):
                for granularity in self._granularities:
                    yield _drama_unit(drama, geo, granularity, priority, item="title")


def with_related(units: Sequence[QueryUnit], *, related: bool, every: int = RELATED_EVERY) -> tuple[QueryUnit, ...]:
    """Every `every`-th drama unit (controls and titles, in the given order) also asks for related queries; market
    series never do, and none does when `related` is off (route a_only)."""
    if not related:
        return tuple(units)
    kept, dramas = [], 0
    for unit in units:
        if unit.identity is not None:
            kept.append(replace(unit, related=dramas % every == 0))
            dramas += 1
        else:
            kept.append(unit)
    return tuple(kept)
