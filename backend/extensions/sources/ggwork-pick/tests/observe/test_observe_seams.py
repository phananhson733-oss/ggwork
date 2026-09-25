"""Batch 0 seams: TR-01 named the versions and TR-33 wrote the shared contract in parallel (plan section 6).

A set judged under the names in versions.py is frozen into FrozenInputs and the pick_obs views, so every name has to
fit the contract field that carries it, and the current link-rules version needs the pairing bound the contract keeps
per version. Either side changing alone turns this red before a collector writes a row the contract refuses.
"""

import pytest
from pydantic import TypeAdapter

from ggwork_pick.observe import contract, contract_rows, versions

NAMED = {
    "COLLECTOR_VERSION": (contract.CollectorVersion, versions.COLLECTOR_VERSION),
    "TREND_RULES_VERSION": (contract.RulesVersion, versions.TREND_RULES_VERSION),
    "GSC_RULES_VERSION": (contract.RulesVersion, versions.GSC_RULES_VERSION),
    "WATCH_RULES_VERSION": (contract.RulesVersion, versions.WATCH_RULES_VERSION),
    "LINK_RULES_VERSION": (contract.LinkRulesVersion, versions.LINK_RULES_VERSION),
    "EVAL_RULES_VERSION": (contract.EvalRulesVersion, versions.EVAL_RULES_VERSION),
    "MARKET_MAP_VERSION": (contract.MarketMapVersion, versions.MARKET_MAP_VERSION),
}


@pytest.mark.parametrize("name", sorted(NAMED))
def test_named_versions_fit_the_contract(name):
    shape, value = NAMED[name]
    assert TypeAdapter(shape).validate_python(value) == value


def test_current_link_rules_has_a_pairing_bound():
    assert versions.LINK_RULES_VERSION in contract_rows.LINK_PAIR_MAX_GAP_MINUTES
