"""TR-10: which judgment rows the agent may use (plan D31; design 4.7, 5.8, section 9 #4; contract section 14).

tests/fixtures/obs_contract/obs_eligibility_cases.json (TR-33) is the truth table; TR-27 counts the reasons returned here.
"""

import json
from pathlib import Path

import pytest

from ggwork_pick.observe import contract, contract_rows
from ggwork_pick.observe.eligibility import EXCLUSION_TEXT, Eligibility, agent_eligibility
from ggwork_pick.observe.wording import forbidden_in

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "obs_contract"
TABLE = json.loads((FIXTURES / "obs_eligibility_cases.json").read_text(encoding="utf-8"))
BASES = {case["name"]: case["value"] for case in json.loads((FIXTURES / "state_rows.json").read_text(encoding="utf-8"))["valid"]}
BASE_OF = {"trends": "trends_confirmed", "gsc": "gsc_surge"}


def _row(**fields) -> contract_rows.StateRow:
    return contract_rows.StateRow.model_validate({**BASES[BASE_OF[fields["channel"]]], **fields})


def _conditions(**fields) -> contract.ObsConditionFields:
    return contract.ObsConditionFields.model_validate(fields)


@pytest.mark.parametrize("case", TABLE["cases"], ids=lambda case: case["name"])
def test_eligibility_cases_fixture(case):
    verdict = agent_eligibility(_row(**case["row"]), _conditions(**case["conditions"]))
    assert isinstance(verdict, Eligibility)
    assert (verdict.eligible, list(verdict.reasons)) == (case["expected"]["eligible"], case["expected"]["reasons"])


def test_reasons_follow_the_contract_order():
    assert tuple(TABLE["reasons_order"]) == contract.EXCLUSION_REASONS
    everything = _row(
        channel="trends", state="emerging", confirmation="first", tier="B", correspondence="unconfirmed", id_evidence="weak",
        ambiguity="generic", flags=["control_unavailable", "unstable", "shared_title"], carried_over=True, stale=True,
    )  # fmt: skip
    reasons = agent_eligibility(everything, _conditions()).reasons
    assert list(reasons) == sorted(reasons, key=contract.EXCLUSION_REASONS.index)
    assert "correspondence_unconfirmed" in reasons and "correspondence_presumed" not in reasons


def test_gsc_only_no_correspondence_needed():
    """A GSC row's page belongs to the drama by its URL: no correspondence, ambiguity or tier is asked, whatever the conditions."""
    row = _row(channel="gsc", state="surge", admission="formal")
    for conditions in (_conditions(), _conditions(trend_include_presumed=True), _conditions(trend_state="emerging", trend_include_first=True)):
        assert agent_eligibility(row, conditions) == Eligibility(())
    # Only admission and the two mapping flags exclude it.
    flagged = _row(channel="gsc", state="present", admission="descriptive", labels=[], flags=["detail_gap", "stale_slice", "short_history"])
    assert agent_eligibility(flagged, _conditions()).reasons == ("gsc_descriptive_only",)


def test_presumed_requires_flag():
    """Design 4.7 and section 9 #4: a strong, unconfirmed correspondence enters only when trend_include_presumed asks for it."""
    presumed = _row(channel="trends", correspondence="unconfirmed", id_evidence="strong")
    assert agent_eligibility(presumed, _conditions()).reasons == ("correspondence_presumed",)
    assert agent_eligibility(presumed, _conditions(trend_include_presumed=True)).eligible
    # trend_include_first is a separate dimension (D31): it does not admit a presumed correspondence.
    assert agent_eligibility(presumed, _conditions(trend_include_first=True)).reasons == ("correspondence_presumed",)
    for evidence in ("medium", "weak"):
        weaker = _row(channel="trends", correspondence="unconfirmed", id_evidence=evidence)
        assert agent_eligibility(weaker, _conditions(trend_include_presumed=True)).reasons == ("correspondence_unconfirmed",)


def test_first_needs_its_own_flag():
    first = _row(channel="trends", confirmation="first")
    assert agent_eligibility(first, _conditions()).reasons == ("trend_first_only",)
    assert agent_eligibility(first, _conditions(trend_include_first=True)).eligible
    assert agent_eligibility(first, _conditions(trend_include_presumed=True)).reasons == ("trend_first_only",)


@pytest.mark.parametrize("state", ["cooling", "flat", "sparse", "insufficient_window", "ambiguous", "failed"])
def test_only_rising_and_emerging_trends_rows_are_asked(state):
    row = _row(channel="trends", state=state, confirmation=None)
    with pytest.raises(ValueError, match="rising"):
        agent_eligibility(row, _conditions())


def test_exclusion_text_covers_every_reason():
    assert tuple(EXCLUSION_TEXT) == contract.EXCLUSION_REASONS
    assert all(text and not forbidden_in(text) for text in EXCLUSION_TEXT.values())
