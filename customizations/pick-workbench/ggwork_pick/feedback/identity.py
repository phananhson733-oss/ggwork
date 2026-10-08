"""Exact, version-scoped catalog identity resolution; titles are never keys."""

import json
from collections import defaultdict
from dataclasses import dataclass

from ggwork_pick.feedback.mapping import canonical_identity
from ggwork_pick.feedback.normalize import LANGUAGES, FeedbackDataset


@dataclass(frozen=True)
class CatalogBinding:
    drama_record_ids: tuple[str, ...] = ()
    method: str = "none"
    status: str = "unmatched"
    evidence_drama_ids: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


def bind_catalog(dataset: FeedbackDataset, catalog_rows, confirmed_links=()) -> dict[str, CatalogBinding]:
    if dataset.transform_version == "feedback-v2":
        return _bind_v2(dataset, catalog_rows)
    result, claimed = {}, {}
    for row in catalog_rows:
        identity = row["identity"]
        explicit = [link for link in confirmed_links if link.get("identity") == identity and link.get("status") == "confirmed"]
        posted = row.get("posted") or {}
        sd_ids = set(posted.get("records", [])) if posted.get("matched") is True else set()
        refs = {link["drama_record_id"] for link in explicit}
        method = "confirmed_link" if explicit else "catalog_sd"
        unresolved = False
        if not explicit:
            for sd in sd_ids:
                matched = [drama.record_id for drama in dataset.dramas.values() if drama.sd == sd]
                if len(matched) != 1:
                    unresolved = True
                refs.update(matched)
        for ref in refs:
            claimed.setdefault(ref, set()).add(identity)
        language = LANGUAGES.get(row.get("language"), row.get("language"))
        theater = row.get("theater")
        compatible = bool(language and theater) and all(
            ref in dataset.dramas and dataset.dramas[ref].language == language and (dataset.dramas[ref].theater or "").casefold() == theater.casefold()
            for ref in refs
        )
        if len(refs) == 1 and compatible and not unresolved:
            result[identity] = CatalogBinding(tuple(sorted(refs)), method, "confirmed")
        else:
            result[identity] = CatalogBinding((), method if refs or sd_ids else "none", "ambiguous" if refs or sd_ids else "unmatched")
    for identity, binding in list(result.items()):
        if any(len(claimed[ref]) > 1 for ref in binding.drama_record_ids):
            result[identity] = CatalogBinding((), binding.method, "ambiguous")
    return result


def _bind_v2(dataset, catalog_rows):
    claims = defaultdict(list)
    catalog_claims = defaultdict(list)
    for drama in dataset.dramas.values():
        identity = canonical_identity(drama.catalog_identity)
        if identity:
            claims[identity].append(drama)
    for row in catalog_rows:
        catalog_claims[canonical_identity(row["identity"])].append(row)
    result = {}
    for row in catalog_rows:
        identity = canonical_identity(row["identity"])
        dramas = claims.get(identity, [])
        warnings = set()
        language = LANGUAGES.get(row.get("language"), row.get("language"))
        theater = row.get("theater")
        if identity is None:
            warnings.add("master_identity_invalid")
        if len(dramas) > 1 or len(catalog_claims[identity]) > 1:
            warnings.add("master_identity_conflict")
        for drama in dramas:
            if drama.catalog_status != "已确认":
                warnings.add("master_identity_conflict" if drama.catalog_status == "有冲突" else "master_identity_unconfirmed")
            if (
                not language
                or not theater
                or json.loads(identity)[2] != language
                or drama.language != language
                or (drama.theater or "").casefold() != theater.casefold()
            ):
                warnings.add("master_identity_incompatible")
        confirmed = len(dramas) == 1 and not warnings
        result[row["identity"]] = CatalogBinding(
            (dramas[0].record_id,) if confirmed else (),
            "confirmed_master" if dramas else "none",
            "confirmed" if confirmed else "ambiguous" if dramas or warnings else "unmatched",
            tuple(sorted(drama.record_id for drama in dramas)),
            tuple(sorted(warnings)),
        )
    return result
