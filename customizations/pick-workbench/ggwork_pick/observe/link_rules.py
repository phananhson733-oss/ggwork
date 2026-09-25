"""link-rules-v1: the two channels linked within one country (plan TR-10; design 6.1; D13, D29, D39; contract section 8).

Facts and actionability are kept apart (D13). link_facts runs when a set is published, for the new set and the other
channel's latest set of the same mode, and its facts are frozen into ggwp_obs_links; the agent and the data page read
those rows and never recompute them. link_actionable runs at judgment time with an explicit now: the agent at query time
(materialised into its evidence), the data page at request time. With no new set, a fact stays the same while its action
is withdrawn once the Trends set is over 26 hours old or the GSC set over 6.

The rules, as contract section 8.1 writes them:
- Same country only. A Trends geo pairs with one GSC country through the frozen market map (WW with ALL); countries no geo
  pairs with (the Philippines, India) take no part.
- A Trends row flagged unstable voids its country. GSC rows flagged migration_suspect, mapping_changed, gap_exceeded or
  unverifiable are dropped. When GSC fell back to [hour,page] plus daily countries, a country's 24-hour rows are ignored
  as if absent (the site total's are not: it never had a country dimension to lose).
- up(t): rising and confirmed. low(t): no row, or flat or sparse; first and emerging are neither. gsc_up: a formal row
  with a formal surge or from_zero (24 h) or rising (7 d) label. gsc_low: nothing dropped and no label at all left, so a
  descriptive or informal hit (a small-base surge) is neither up nor low.
- First that holds: cooling; both_rising; trends_lead_page (up, gsc_low, a current drama page on the site);
  trends_lead_distribution (up, no page); site_only (low, gsc_up); otherwise no fact.
- WW up with ALL gsc_up is global_parallel, never both_rising. With no both_rising, one country up and another gsc_up
  adds different_markets. Neither ever carries an action.

Every version is a LinkRules entry in LINK_RULES; a pair or a stored row names its version and an unknown one is refused
(D29), never run as the newest.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from types import MappingProxyType
from typing import Any, Protocol

from ggwork_pick.observe.contract import (
    LINK_ACTIONABILITY_REASONS,
    LINK_EXCLUDED_FLAGS,
    LINK_STATES,
    GscSetRef,
    LinkActionabilityReason,
    TrendsSetRef,
    first_problem,
)
from ggwork_pick.observe.contract_rows import LINK_PAIR_MAX_GAP_MINUTES, LinkFact, StateRow
from ggwork_pick.observe.instants import MINUTE, instant, whole_minutes
from ggwork_pick.observe.market_map import market_map

GLOBAL_GEO, GLOBAL_COUNTRY = "WW", "ALL"
HOUR = timedelta(hours=1)


@dataclass(frozen=True, slots=True)
class LinkPair:
    """The two sets a link pairs (D13): TR-20 passes the new set and the other channel's latest set of the same mode."""

    link_rules_version: str
    trends: TrendsSetRef
    gsc: GscSetRef
    degraded_no_country_24h: bool

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> "LinkPair":
        """The fixture's shape: the GSC side carries degraded_no_country_24h next to its set reference."""
        degraded = data["gsc"]["degraded_no_country_24h"]
        if not isinstance(degraded, bool):
            raise ValueError("degraded_no_country_24h 是布尔值")
        gsc = {key: value for key, value in data["gsc"].items() if key != "degraded_no_country_24h"}
        return cls(data["link_rules_version"], TrendsSetRef.model_validate(data["trends"]), GscSetRef.model_validate(gsc), degraded)


@dataclass(frozen=True, slots=True)
class Actionability:
    reasons: tuple[LinkActionabilityReason, ...]

    @property
    def actionable(self) -> bool:
        return not self.reasons


class LinkFactLike(Protocol):
    """What actionability reads from a fact: a LinkFact, a LinkRow, or the TS copy's three fields."""

    label: str
    timely: bool
    stale: bool


@dataclass(frozen=True, slots=True)
class _Country:
    """One country (or the global pair) after the exclusions; kept lists the GSC rows left, 24 h first."""

    country: str
    geo: str
    trends: StateRow | None
    kept: tuple[StateRow, ...]
    ups: tuple[StateRow, ...]
    t_up: bool
    t_low: bool
    g_low: bool

    @property
    def reference(self) -> StateRow | None:
        """The GSC row a fact cites: the first gsc_up row, else the first row left."""
        return (self.ups or self.kept or (None,))[0]


def _check_rows(trends: tuple[StateRow, ...], gsc: tuple[StateRow, ...], pair: LinkPair) -> None:
    rows = (*trends, *gsc)
    problem = first_problem(
        (
            (all(r.channel == "trends" for r in trends) and all(r.channel == "gsc" for r in gsc), "trends 行与 gsc 行的 channel 不对"),
            (len({r.identity for r in rows}) <= 1, "联动只看一个身份的行"),
            (all(r.set_id == pair.trends.set_id for r in trends) and all(r.set_id == pair.gsc.set_id for r in gsc), "行不属于配对的两个集合"),
            (len({r.mode for r in rows}) <= 1, "同模式配对：shadow 只配 shadow，live 只配 live（行的模式不一致）"),
            (len({r.scope for r in trends}) == len(trends), "同一个 geo 只能有一行 Trends"),
            (len({(r.scope, r.window_kind) for r in gsc}) == len(gsc), "同一国家与窗口只能有一行 GSC"),
        )
    )
    if problem is not None:
        raise ValueError(problem)


@dataclass(frozen=True, slots=True)
class LinkRules:
    version: str
    market_map_version: str
    pair_max_gap: timedelta
    trends_max_age: timedelta
    gsc_max_age: timedelta
    up_labels: MappingProxyType
    low_states: tuple[str, ...]
    excluded_flags: tuple[str, ...]

    @property
    def geo_country(self) -> MappingProxyType:
        return market_map(self.market_map_version).geo_country

    def params(self) -> dict[str, Any]:
        """The parameters in obs_link_cases.json's form, which the TS copy reads."""
        return {
            "link_rules_version": self.version,
            "pair_max_gap_hours": self.pair_max_gap // HOUR,
            "trends_max_age_hours": self.trends_max_age // HOUR,
            "gsc_max_age_hours": self.gsc_max_age // HOUR,
            "geo_country": dict(self.geo_country),
            "up_labels": {kind: list(labels) for kind, labels in self.up_labels.items()},
            "low_states": list(self.low_states),
            "excluded_flags": list(self.excluded_flags),
        }

    def facts(self, trends: Sequence[StateRow], gsc: Sequence[StateRow], pair: LinkPair, *, has_site_page: bool) -> tuple[LinkFact, ...]:
        trends, gsc = tuple(trends), tuple(gsc)
        _check_rows(trends, gsc, pair)
        by_geo = {row.scope: row for row in trends}
        sides = self._countries(by_geo, gsc, pair)
        found = tuple(
            self._fact(side.country, side.geo, label, side.trends, side.reference, pair)
            for side in sides
            if (label := self._label(side, has_site_page)) is not None
        )
        extra = (*self._global(by_geo.get(GLOBAL_GEO), gsc, pair), *self._different_markets(found, sides, pair))
        return tuple(sorted((*found, *extra), key=lambda fact: (fact.country is None, fact.country or "")))

    def actionable(self, fact: LinkFactLike, trends_published_at: str | datetime, gsc_published_at: str | datetime, now: datetime) -> Actionability:
        moment = instant(now)
        checks = {
            "label_not_actionable": fact.label not in LINK_STATES,
            "untimely_pair": not fact.timely,
            "stale_row": fact.stale,
            "trends_set_too_old": moment - instant(trends_published_at) > self.trends_max_age,
            "gsc_set_too_old": moment - instant(gsc_published_at) > self.gsc_max_age,
        }
        return Actionability(tuple(reason for reason in LINK_ACTIONABILITY_REASONS if checks[reason]))

    def _countries(self, by_geo: dict[str, StateRow], gsc: tuple[StateRow, ...], pair: LinkPair) -> tuple[_Country, ...]:
        geo_of = {country: geo for geo, country in self.geo_country.items() if country != GLOBAL_COUNTRY}
        present = {self.geo_country.get(geo) for geo in by_geo} | {row.scope for row in gsc}
        sides = (
            self._side(country, geo_of[country], by_geo.get(geo_of[country]), self._rows_of(country, gsc, pair)) for country in sorted(present & set(geo_of))
        )
        return tuple(side for side in sides if side is not None)

    @staticmethod
    def _rows_of(country: str, gsc: tuple[StateRow, ...], pair: LinkPair) -> tuple[StateRow, ...]:
        """A country's GSC rows; after a fallback to [hour,page] its 24-hour rows are ignored, as if absent."""
        return tuple(row for row in gsc if row.scope == country and not (pair.degraded_no_country_24h and row.window_kind == "24h"))

    def _side(self, country: str, geo: str, trends: StateRow | None, rows: tuple[StateRow, ...]) -> _Country | None:
        excluded = set(self.excluded_flags)
        if trends is not None and excluded & set(trends.flags):
            return None
        kept = tuple(sorted((row for row in rows if not excluded & set(row.flags)), key=lambda row: row.window_kind != "24h"))
        return _Country(
            country=country,
            geo=geo,
            trends=trends,
            kept=kept,
            ups=tuple(row for row in kept if self._gsc_up(row)),
            t_up=trends is not None and trends.state == "rising" and trends.confirmation == "confirmed",
            t_low=trends is None or trends.state in self.low_states,
            g_low=len(kept) == len(rows) and not any(row.labels for row in kept),
        )

    def _gsc_up(self, row: StateRow) -> bool:
        wanted = self.up_labels.get(row.window_kind, ())
        return row.admission == "formal" and any(hit.formal and hit.label in wanted for hit in row.labels)

    @staticmethod
    def _label(side: _Country, has_site_page: bool) -> str | None:
        rules = (
            ("cooling", side.trends is not None and side.trends.state == "cooling"),
            ("both_rising", side.t_up and bool(side.ups)),
            ("trends_lead_page", side.t_up and side.g_low and has_site_page),
            ("trends_lead_distribution", side.t_up and not has_site_page),
            ("site_only", side.t_low and bool(side.ups)),
        )
        return next((label for label, holds in rules if holds), None)

    def _global(self, trends: StateRow | None, gsc: tuple[StateRow, ...], pair: LinkPair) -> tuple[LinkFact, ...]:
        """WW and the site total rising together are parallel only (counterexample 19): shown, never both_rising."""
        side = self._side(GLOBAL_COUNTRY, GLOBAL_GEO, trends, tuple(row for row in gsc if row.scope == GLOBAL_COUNTRY))
        if side is None or not (side.t_up and side.ups):
            return ()
        return (self._fact(GLOBAL_COUNTRY, GLOBAL_GEO, "global_parallel", side.trends, side.ups[0], pair),)

    def _different_markets(self, found: tuple[LinkFact, ...], sides: tuple[_Country, ...], pair: LinkPair) -> tuple[LinkFact, ...]:
        if any(fact.label == "both_rising" for fact in found):
            return ()
        trends_up = {side.country for side in sides if side.t_up}
        gsc_up = {side.country for side in sides if side.ups}
        if not any(one != other for one in trends_up for other in gsc_up):
            return ()
        return (self._fact(None, None, "different_markets", None, None, pair),)

    def _fact(self, country, geo, label, trends: StateRow | None, reference: StateRow | None, pair: LinkPair) -> LinkFact:
        trends_anchor = trends.latest_block_end if trends is not None else pair.trends.latest_block_end
        gsc_anchor = reference.window_end if reference is not None else pair.gsc.cutoff
        gap = abs(whole_minutes(instant(gsc_anchor) - instant(trends_anchor)))
        return LinkFact(
            country=country,
            trends_geo=geo,
            label=label,
            trends_row_id=None if trends is None else trends.row_id,
            gsc_row_id=None if reference is None else reference.row_id,
            trends_anchor=trends_anchor,
            gsc_anchor=gsc_anchor,
            pair_gap_minutes=gap,
            timely=gap <= self.pair_max_gap // MINUTE,
            published_gap_minutes=abs(whole_minutes(instant(pair.gsc.published_at) - instant(pair.trends.published_at))),
            stale=trends is not None and (trends.carried_over or trends.stale),
        )


LINK_RULES_V1 = LinkRules(
    version="link-rules-v1",
    market_map_version="market-map-v1",
    pair_max_gap=timedelta(minutes=LINK_PAIR_MAX_GAP_MINUTES["link-rules-v1"]),  # the contract registers the bound
    trends_max_age=timedelta(hours=26),
    gsc_max_age=timedelta(hours=6),
    up_labels=MappingProxyType({"24h": ("surge", "from_zero"), "7d": ("rising",)}),
    low_states=("flat", "sparse"),
    excluded_flags=LINK_EXCLUDED_FLAGS,
)
LINK_RULES = MappingProxyType({LINK_RULES_V1.version: LINK_RULES_V1})


def link_rules(version: str) -> LinkRules:
    """The rules a pair or a stored link row was judged with (D29): an unknown version is refused."""
    found = LINK_RULES.get(version)
    if found is None:
        raise LookupError(f"未登记的 link-rules 版本：{version}")
    return found


def link_facts(trends: Sequence[StateRow], gsc: Sequence[StateRow], pair: LinkPair, *, has_site_page: bool) -> tuple[LinkFact, ...]:
    """One identity's link facts for the pair, by the pair's link-rules version; each country at most once, sorted by
    country with different_markets last. has_site_page: the identity has a current drama page on the site."""
    return link_rules(pair.link_rules_version).facts(trends, gsc, pair, has_site_page=has_site_page)


def link_actionable(fact: LinkFactLike, trends_published_at: str | datetime, gsc_published_at: str | datetime, now: datetime, *, version: str) -> Actionability:
    """Whether a frozen fact is actionable at `now`, with every reason it is not, in contract order. Exactly 26 or 6 hours
    still holds. `version` is the fact's link-rules version (a LinkRow's link_rules_version)."""
    return link_rules(version).actionable(fact, trends_published_at, gsc_published_at, now)
