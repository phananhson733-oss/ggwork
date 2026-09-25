"""TR-10: link-rules-v1, the same-country link facts and their judgment-time actionability (design 6.1; plan D13, D29, D39).

tests/fixtures/obs_contract/obs_link_cases.json (TR-33) is the shared truth: link_facts reproduces every facts case and
link_actionable every actionable case, which the data page's TS copy (TR-24 obs-link.ts) reproduces too. The named tests
below pin counterexamples 4 and 19 of design section 8 on their own rows.
"""

import json
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from ggwork_pick.observe import contract, contract_rows, versions
from ggwork_pick.observe.link_rules import LINK_RULES, LinkPair, link_actionable, link_facts, link_rules
from ggwork_pick.observe.market_map import market_map

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "obs_contract"
CASES = json.loads((FIXTURES / "obs_link_cases.json").read_text(encoding="utf-8"))
BASES = {case["name"]: case["value"] for case in json.loads((FIXTURES / "state_rows.json").read_text(encoding="utf-8"))["valid"]}
V1 = "link-rules-v1"
BLOCK_END = "2026-09-24T17:00:00.000000+00:00"
W0_END = "2026-09-24T18:00:00.000000+00:00"


def _row(base: str, **fields) -> contract_rows.StateRow:
    return contract_rows.StateRow.model_validate({**BASES[base], **fields})


def _trends(scope: str, state: str = "rising", confirmation: str | None = "confirmed", **fields) -> contract_rows.StateRow:
    return _row("trends_confirmed", scope=scope, state=state, confirmation=confirmation, **fields)


def _gsc(scope: str, window_kind: str = "24h", label: str | None = "surge", formal: bool = True, **fields) -> contract_rows.StateRow:
    labels = [] if label is None else [{"label": label, "formal": formal, "condition": "见 gsc-rules-v1", "counts": {}}]
    state = label if (label and formal) else "present"
    return _row("gsc_surge", scope=scope, window_kind=window_kind, state=state, labels=labels, **fields)


def _pair(**changes) -> LinkPair:
    default = CASES["default_pair"]
    gsc = {**default["gsc"], **changes.pop("gsc", {})}
    trends = {**default["trends"], **changes.pop("trends", {})}
    return LinkPair.from_mapping({**default, "trends": trends, "gsc": gsc, **changes})


def _facts(trends=(), gsc=(), pair=None, page=True) -> list[tuple]:
    found = link_facts(tuple(trends), tuple(gsc), pair or _pair(), has_site_page=page)
    return [(fact.country, fact.label) for fact in found]


# ---- the shared fixture ---------------------------------------------------------------------------------------------


def _case_rows(given: dict) -> tuple[tuple, tuple]:
    trends = tuple(_row("trends_confirmed", **row) for row in given["trends"])
    gsc = tuple(_row("gsc_surge", **row) for row in given["gsc"])
    return trends, gsc


@pytest.mark.parametrize("case", CASES["facts_cases"], ids=lambda case: case["name"])
def test_link_cases_fixture_facts(case):
    given = case["input"]
    trends, gsc = _case_rows(given)
    pair = LinkPair.from_mapping(given.get("pair", CASES["default_pair"]))
    facts = link_facts(trends, gsc, pair, has_site_page=given["has_site_page"])
    assert all(type(fact) is contract_rows.LinkFact for fact in facts)
    assert [fact.model_dump() for fact in facts] == case["facts"]


@pytest.mark.parametrize("case", CASES["actionable_cases"], ids=lambda case: case["name"])
def test_link_cases_fixture_actionable(case):
    fact = SimpleNamespace(**case["fact"])
    verdict = link_actionable(fact, case["trends_published_at"], case["gsc_published_at"], datetime.fromisoformat(case["now"]), version=V1)
    assert (verdict.actionable, list(verdict.reasons)) == (case["expected"]["actionable"], case["expected"]["reasons"])


def test_link_params_match_fixture():
    """The fixture's params are link-rules-v1's own, and the pairing bound is the one the contract registers (TR-33)."""
    rules = link_rules(V1)
    assert rules.params() == CASES["params"]
    assert rules.pair_max_gap == timedelta(minutes=contract_rows.LINK_PAIR_MAX_GAP_MINUTES[V1])
    assert dict(rules.geo_country) == dict(market_map(rules.market_map_version).geo_country)
    assert set(LINK_RULES) == set(contract_rows.LINK_PAIR_MAX_GAP_MINUTES)
    assert versions.LINK_RULES_VERSION in LINK_RULES


def test_unknown_link_rules_refused():
    """D29: a version with no registered implementation is refused, never run as the newest one."""
    with pytest.raises(LookupError, match="link-rules-v9"):
        link_rules("link-rules-v9")
    with pytest.raises(LookupError):
        link_facts((), (), _pair(link_rules_version="link-rules-v9"), has_site_page=True)
    fact = SimpleNamespace(label="both_rising", timely=True, stale=False)
    with pytest.raises(LookupError):
        link_actionable(fact, BLOCK_END, W0_END, datetime.fromisoformat(W0_END), version="link-rules-v9")


# ---- counterexamples 4 and 19 ---------------------------------------------------------------------------------------


def test_link_same_country_only():
    """Counterexample 4: US Trends rising and MEX GSC rising are never both_rising; each country keeps its own fact."""
    facts = _facts(trends=[_trends("US")], gsc=[_gsc("MEX")])
    assert facts == [("MEX", "site_only"), ("USA", "trends_lead_page"), (None, "different_markets")]
    assert _facts(trends=[_trends("US")], gsc=[_gsc("USA")]) == [("USA", "both_rising")]
    # Two countries rising on both sides pair within each country, and different_markets is not added.
    both = _facts(trends=[_trends("US", row_id=41), _trends("MX", row_id=42)], gsc=[_gsc("USA", row_id=907), _gsc("MEX", row_id=908)])
    assert both == [("MEX", "both_rising"), ("USA", "both_rising")]


def test_cooling_blocks_same_country_only():
    """Counterexample 4: US cooling blocks US; GB, rising on both sides, is still both_rising."""
    facts = _facts(
        trends=[_trends("US", state="cooling", confirmation=None, row_id=41), _trends("GB", row_id=42)],
        gsc=[_gsc("USA", row_id=907), _gsc("GBR", row_id=909)],
    )
    assert facts == [("GBR", "both_rising"), ("USA", "cooling")]
    # Cooling without any GSC row is still the country's fact, and nothing else is blocked.
    assert _facts(trends=[_trends("US", state="cooling", confirmation=None, row_id=41), _trends("GB", row_id=42)]) == [
        ("GBR", "trends_lead_page"),
        ("USA", "cooling"),
    ]


def test_ww_sitewide_parallel_only():
    """Counterexample 19: WW rising and the site total rising are only parallel, never both_rising, and carry no action."""
    facts = link_facts((_trends("WW", row_id=44),), (_gsc("ALL", row_id=915),), _pair(), has_site_page=True)
    assert [(f.country, f.trends_geo, f.label) for f in facts] == [("ALL", "WW", "global_parallel")]
    verdict = link_actionable(facts[0], "2026-09-25T01:52:10.000000+00:00", "2026-09-25T03:31:40.000000+00:00", datetime.fromisoformat(W0_END), version=V1)
    assert not verdict.actionable and verdict.reasons[0] == "label_not_actionable"
    # WW up while the site total rises in some country: the global pair never feeds a country's fact.
    assert _facts(trends=[_trends("WW", row_id=44)], gsc=[_gsc("MEX", row_id=908)]) == [("MEX", "site_only")]


# ---- timeliness, degradation, exclusions ----------------------------------------------------------------------------


def test_link_pair_timeliness():
    """D39: the pairing's own bound is between GSC W0's end and the Trends latest block end, for either block definition."""
    hourly_far = _trends("US", latest_block_end="2026-09-22T17:00:00.000000+00:00")
    (fact,) = link_facts((hourly_far,), (_gsc("USA"),), _pair(), has_site_page=True)
    assert (fact.pair_gap_minutes, fact.timely) == (49 * 60, False)
    hourly_edge = _trends("US", latest_block_end="2026-09-22T18:00:00.000000+00:00")
    (fact,) = link_facts((hourly_edge,), (_gsc("USA"),), _pair(), has_site_page=True)
    assert (fact.pair_gap_minutes, fact.timely) == (48 * 60, True)
    daily = _trends("US", window_kind="D", latest_block_end="2026-09-24T00:00:00.000000+00:00")
    (fact,) = link_facts((daily,), (_gsc("USA"),), _pair(), has_site_page=True)
    assert (fact.trends_anchor, fact.pair_gap_minutes, fact.timely) == ("2026-09-24T00:00:00.000000+00:00", 18 * 60, True)
    daily_old = _trends("US", window_kind="D", latest_block_end="2026-09-22T00:00:00.000000+00:00")
    (fact,) = link_facts((daily_old,), (_gsc("USA"),), _pair(), has_site_page=True)
    assert (fact.pair_gap_minutes, fact.timely) == (66 * 60, False)


def test_link_actionability_read_time():
    """D13: with no new set, now crossing 6 h (GSC) or 26 h (Trends) withdraws the action; the fact itself is unchanged."""
    (fact,) = link_facts((_trends("US"),), (_gsc("USA"),), _pair(), has_site_page=True)
    frozen = fact.model_dump()
    trends_at, gsc_at = "2026-09-25T01:52:10.000000+00:00", "2026-09-25T03:31:40.000000+00:00"
    moments = {
        datetime.fromisoformat(gsc_at) + timedelta(hours=1): (),
        datetime.fromisoformat(gsc_at) + timedelta(hours=6, seconds=1): ("gsc_set_too_old",),
        datetime.fromisoformat(trends_at) + timedelta(hours=26, seconds=1): ("trends_set_too_old", "gsc_set_too_old"),
    }
    for now, reasons in moments.items():
        verdict = link_actionable(fact, trends_at, gsc_at, now, version=V1)
        assert (verdict.actionable, verdict.reasons) == (not reasons, reasons)
    assert fact.model_dump() == frozen


def test_link_degraded_no_country_24h():
    """GSC fell back to [hour,page]: per-country 24 h rows do not link, 7 d rows still do; the site total is unaffected."""
    degraded = _pair(gsc={"degraded_no_country_24h": True})
    assert _facts(trends=[_trends("US")], gsc=[_gsc("USA")], pair=degraded) == [("USA", "trends_lead_page")]
    week = _gsc("USA", window_kind="7d", label="rising", row_id=916, window_end="2026-09-24T07:00:00.000000+00:00")
    assert _facts(trends=[_trends("US")], gsc=[_gsc("USA"), week], pair=degraded) == [("USA", "both_rising")]
    assert _facts(trends=[_trends("WW", row_id=44)], gsc=[_gsc("ALL", row_id=915)], pair=degraded) == [("ALL", "global_parallel")]
    # Ignored is not dropped: without a page a country with only 24 h rows is still Trends-led distribution.
    assert _facts(trends=[_trends("US")], gsc=[_gsc("USA")], pair=degraded, page=False) == [("USA", "trends_lead_distribution")]


@pytest.mark.parametrize("flag", ["migration_suspect", "mapping_changed", "gap_exceeded", "unverifiable"])
def test_link_excluded_flags(flag):
    """A flagged GSC row is dropped: it neither links nor counts as low. Trends unstable voids the whole country."""
    assert _facts(trends=[_trends("US")], gsc=[_gsc("USA", flags=[flag])]) == []
    assert _facts(gsc=[_gsc("USA", flags=[flag])]) == []
    assert tuple(link_rules(V1).excluded_flags) == contract.LINK_EXCLUDED_FLAGS


def test_link_unstable_voids_country():
    unstable = _trends("US", confirmation="first", flags=["unstable"])
    assert _facts(trends=[unstable], gsc=[_gsc("USA")]) == []
    # Voided means absent from different_markets as well.
    assert _facts(trends=[unstable], gsc=[_gsc("MEX", row_id=908)]) == [("MEX", "site_only")]


def test_link_fact_refs_and_staleness():
    carried = _trends("US", carried_over=True, stale=True, latest_block_end="2026-09-21T17:00:00.000000+00:00")
    (fact,) = link_facts((carried,), (_gsc("USA", label=None),), _pair(), has_site_page=True)
    assert (fact.label, fact.stale, fact.gsc_row_id, fact.trends_row_id) == ("trends_lead_page", True, 907, 41)
    # No GSC row: the anchor is the set's cutoff; no Trends row: the set's latest block end.
    (fact,) = link_facts((), (_gsc("USA", row_id=911, label="from_zero"),), _pair(), has_site_page=True)
    assert (fact.trends_row_id, fact.trends_anchor, fact.gsc_anchor) == (None, BLOCK_END, W0_END)


@pytest.mark.parametrize(
    ("trends", "gsc", "message"),
    [
        ((_row("gsc_surge"),), (), "channel"),
        ((), (_row("trends_confirmed"),), "channel"),
        ((_trends("US"), _trends("US", row_id=42)), (), "geo"),
        ((), (_gsc("USA"), _gsc("USA", row_id=918)), "国家与窗口"),
        ((_trends("US"),), (_gsc("USA", identity='["kalostv","x","en"]'),), "身份"),
        ((_trends("US", set_id="0" * 32),), (), "集合"),
        ((), (_gsc("USA", set_id="0" * 32),), "集合"),
        ((_trends("US", mode="shadow"),), (_gsc("USA"),), "模式"),
    ],
)
def test_link_inputs_checked(trends, gsc, message):
    """The rows are one identity's, from the paired sets, of one mode (D13): anything else is a caller bug, refused."""
    with pytest.raises(ValueError, match=message):
        link_facts(trends, gsc, _pair(), has_site_page=True)


def test_link_actionable_needs_aware_now():
    fact = SimpleNamespace(label="both_rising", timely=True, stale=False)
    with pytest.raises(ValueError, match="时区"):
        link_actionable(fact, BLOCK_END, W0_END, datetime(2026, 9, 25, 4), version=V1)


def test_link_pair_from_mapping_checks_degraded():
    pair = json.loads(json.dumps(CASES["default_pair"]))
    with pytest.raises(ValueError, match="degraded_no_country_24h"):
        LinkPair.from_mapping({**pair, "gsc": {**pair["gsc"], "degraded_no_country_24h": "false"}})
