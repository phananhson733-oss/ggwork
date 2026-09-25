"""TR-10: market-map-v1, the display groups and the geo to alpha-3 pairing (design 1.3, 5.7, 6.1; plan section 9 #2)."""

import json
from pathlib import Path

import pytest

from ggwork_pick.observe import contract_rows, versions
from ggwork_pick.observe.link_rules import LinkPair, link_facts
from ggwork_pick.observe.market_map import MARKET_MAPS, market_map

V1 = market_map("market-map-v1")
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "obs_contract"
LINKS = json.loads((FIXTURES / "obs_link_cases.json").read_text(encoding="utf-8"))
MAP_FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "obs_market_map.json"
BASES = {case["name"]: case["value"] for case in json.loads((FIXTURES / "state_rows.json").read_text(encoding="utf-8"))["valid"]}


def _row(base: str, **fields) -> contract_rows.StateRow:
    return contract_rows.StateRow.model_validate({**BASES[base], **fields})


def test_market_map_display_only():
    """Groups only arrange the display: a Trends geo links with its own country, never with the rest of its group."""
    north_america = V1.group_of_country("CAN")
    assert north_america is V1.group_of_country("USA") and north_america.geos == ("US",)
    trends = (_row("trends_confirmed", scope="US"),)
    canada = (_row("gsc_surge", scope="CAN"),)
    pair = LinkPair.from_mapping(LINKS["default_pair"])
    facts = link_facts(trends, canada, pair, has_site_page=True)
    assert [(fact.country, fact.label) for fact in facts] == [("USA", "trends_lead_page")]
    ireland = (_row("gsc_surge", scope="IRL"),)
    gb = (_row("trends_confirmed", scope="GB"),)
    assert [(f.country, f.label) for f in link_facts(gb, ireland, pair, has_site_page=True)] == [("GBR", "trends_lead_page")]


def test_bulgaria_single_with_page_language_note():
    """Design 5.7: BGR stands alone, and its note says the 98.1% figure is a page-language measure, not a country one."""
    group = V1.group_of_country("BGR")
    assert group.countries == ("BGR",) and group.geos == ("BG",)
    assert "保语页面口径" in group.note and "98.1%" in group.note
    assert not group.core


def test_groups_partition_the_listed_countries():
    seen = [country for group in V1.groups for country in group.countries]
    assert len(seen) == len(set(seen))
    assert V1.group_of_country("ALL").key == "global" and V1.group_of_country("ALL").countries == ("ALL",)
    # Philippines and India only count in the site total (design 1.3).
    assert V1.group_of_country("PHL") is None and V1.group_of_country("IND") is None
    assert V1.group_of_country("usa") is None  # alpha-3 is upper case (contract section 16, 6)
    assert {"USA", "CAN"} == set(V1.group_of_country("USA").countries)
    assert {"GBR", "IRL", "AUS", "NZL"} == set(V1.group_of_country("GBR").countries)
    europe = V1.group_of_country("DEU")
    assert {"ESP", "FRA", "ITA", "DEU"} <= set(europe.countries) and not {"GBR", "IRL", "BGR"} & set(europe.countries)
    latin = V1.group_of_country("MEX")
    assert {"MEX", "BRA", "ARG", "COL"} <= set(latin.countries) and not latin.core


def test_core_groups_are_europe_and_north_america():
    """Latin America shows next to the core but is not counted in it; the global row is its own thing (design 1.3)."""
    assert [group.key for group in V1.groups if group.core] == ["north_america", "uk_ie_au_nz", "europe"]
    assert [group.key for group in V1.groups] == ["global", "north_america", "uk_ie_au_nz", "europe", "latin_america", "bulgaria"]


def test_geo_country_pairs():
    assert dict(V1.geo_country) == LINKS["params"]["geo_country"]
    for geo, country in V1.geo_country.items():
        assert V1.country_of_geo(geo) == country and V1.geo_of_country(country) == geo
        assert V1.group_of_geo(geo) is V1.group_of_country(country)
    assert V1.country_of_geo("PH") is None and V1.geo_of_country("PHL") is None


def test_geo_plan():
    """Design 1.3 and section 9 #2: US, MX, BR (and WW for en) may take A; Europe's own geos screen as B first; GB joins in
    the stable period; BG is only sampled in stage 0. The plan is data TR-18 applies."""
    first_round = {plan.geo: plan.first_round for plan in V1.geo_plans}
    assert first_round == {"WW": "A", "US": "A", "GB": None, "ES": "B", "DE": "B", "FR": "B", "IT": "B", "MX": "A", "BR": "A", "BG": None}
    languages = {plan.geo: plan.languages for plan in V1.geo_plans}
    assert languages["WW"] == ("en",) and languages["MX"] == ("es",) and languages["BR"] == ("pt",)
    assert set(first_round) == set(V1.geo_country)


def _display(market) -> dict:
    groups = [{"key": g.key, "label": g.label, "countries": list(g.countries), "geos": list(g.geos), "core": g.core, "note": g.note} for g in market.groups]
    return {"version": market.version, "groups": groups, "geo_country": dict(market.geo_country)}


def test_market_map_fixture():
    """D10: tests/fixtures/obs_market_map.json is every registered market map's display table, in display order, for the
    data page's TS side (TR-24) to read instead of copying some 90 alpha-3 codes by hand; a new version is a new entry."""
    fixture = json.loads(MAP_FIXTURE.read_text(encoding="utf-8"))
    assert fixture["maps"] == [_display(market) for market in MARKET_MAPS.values()]
    assert fixture["about"]


def test_market_map_versions():
    assert versions.MARKET_MAP_VERSION in MARKET_MAPS
    assert V1.version == "market-map-v1"
    with pytest.raises(LookupError, match="market-map-v9"):
        market_map("market-map-v9")
