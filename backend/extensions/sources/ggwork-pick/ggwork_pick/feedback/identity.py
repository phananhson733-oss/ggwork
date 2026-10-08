"""Exact, version-scoped catalog identity resolution; titles are never keys."""

from dataclasses import dataclass

from ggwork_pick.feedback.normalize import LANGUAGES, FeedbackDataset


@dataclass(frozen=True)
class CatalogBinding:
    drama_record_ids: tuple[str, ...] = ()
    method: str = "none"
    status: str = "unmatched"


def bind_catalog(dataset: FeedbackDataset, catalog_rows, confirmed_links=()) -> dict[str, CatalogBinding]:
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
