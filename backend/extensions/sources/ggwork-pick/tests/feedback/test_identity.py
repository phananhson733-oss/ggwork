from ggwork_pick.feedback.identity import bind_catalog
from ggwork_pick.feedback.normalize import normalize

from .fakes import operating_rows, snapshot_from_rows


def test_exact_sd_mapping_requires_language_platform_and_all_references():
    data = normalize(snapshot_from_rows(operating_rows()))
    row = {"identity": "catalog-a", "language": "en", "theater": "ReelShort", "posted": {"matched": True, "records": ["SD-A"]}}
    assert bind_catalog(data, [row])["catalog-a"].drama_record_ids == ("drama-a",)
    for changes in ({"language": "es"}, {"theater": "Other"}, {"posted": {"matched": True, "records": ["SD-A", "missing"]}}):
        assert bind_catalog(data, [{**row, **changes}])["catalog-a"].status != "confirmed"


def test_same_title_is_not_identity_and_source_rows_are_not_mutated():
    data = normalize(snapshot_from_rows(operating_rows()))
    row = {"identity": "catalog-a", "title": "Synthetic Wolf", "language": "en", "theater": "ReelShort"}
    assert bind_catalog(data, [row])["catalog-a"].status == "unmatched"
    assert "posted" not in row


def test_ambiguous_raw_claim_still_blocks_other_catalog_identity():
    data = normalize(snapshot_from_rows(operating_rows()))
    base = {"language": "en", "theater": "ReelShort"}
    rows = [
        {**base, "identity": "a", "posted": {"matched": True, "records": ["SD-A", "SD-B"]}},
        {**base, "identity": "b", "posted": {"matched": True, "records": ["SD-A"]}},
    ]
    assert bind_catalog(data, rows)["b"].status == "ambiguous"
