"""Snapshot-only v2 identity declarations. Audit text is never interpreted."""

import json
from collections import defaultdict
from dataclasses import dataclass


def exact_text(value) -> str | None:
    if isinstance(value, list) and len(value) == 1:
        value = value[0]
    return value if isinstance(value, str) and value.strip() else None


def canonical_identity(value) -> str | None:
    value = exact_text(value)
    if value is None:
        return None
    try:
        parts = json.loads(value)
    except (ValueError, RecursionError):
        return None
    if not isinstance(parts, list) or len(parts) != 3 or not all(isinstance(part, str) and part.strip() for part in parts):
        return None
    return json.dumps(parts, ensure_ascii=False, separators=(",", ":"))


def target_ids(value) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(item["id"] for item in value if isinstance(item, dict) and isinstance(item.get("id"), str) and item["id"])


def compatible_target(value, theater, dramas) -> str | None:
    refs = target_ids(value)
    if not isinstance(value, list) or len(value) != 1 or len(refs) != 1:
        return None
    drama = dramas.get(refs[0])
    if not drama or not theater or not drama.theater or drama.theater.casefold() != theater.casefold():
        return None
    return drama.record_id


@dataclass(frozen=True)
class RevenueResolution:
    drama_id: str | None
    attribution: str
    mapping_ids: tuple[str, ...] = ()
    related_drama_ids: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


class ExternalRegistry:
    def __init__(self, rows, dramas):
        self.dramas = dramas
        self.by_key = defaultdict(list)
        for row in rows:
            source, theater, kind, external = (exact_text(row.get(name)) for name in ("来源系统", "来源剧场", "外部ID类型", "外部ID"))
            if source and theater and kind == "剧目ID" and external:
                self.by_key[(source, theater.casefold(), external)].append(row)

    def resolve(self, row, lane, grain):
        theater = exact_text(row.get("剧场"))
        direct_value = row.get("关联剧集")
        direct = compatible_target(direct_value, theater, self.dramas)
        related = set(target_ids(direct_value))
        warnings, applicable = set(), []
        if direct_value and direct is None:
            warnings.add("revenue_direct_link_invalid")
        if lane == "cps_auto":
            source, external, account = (exact_text(row.get(name)) for name in ("合作方", "来源剧目ID", "账号ID"))
            for mapping in self.by_key.get((source, theater.casefold() if theater else None, external), []):
                scope = exact_text(mapping.get("适用范围"))
                # Unknown scope cannot authorize a match, nor be bypassed by a direct link.
                valid_scope = scope == "全局" or bool(scope and scope.startswith("账号:") and scope[3:].strip())
                if valid_scope and scope != "全局" and (account is None or scope != f"账号:{account}"):
                    continue
                applicable.append(mapping)
                related.update(target_ids(mapping.get("关联剧集")))
                if not valid_scope:
                    warnings.add("external_mapping_scope_unknown")
                state = exact_text(mapping.get("确认状态"))
                if state != "已确认":
                    warnings.add(
                        "external_mapping_inactive"
                        if state == "已停用"
                        else "external_mapping_conflict"
                        if state == "有冲突"
                        else "external_mapping_unconfirmed"
                    )
                if compatible_target(mapping.get("关联剧集"), theater, self.dramas) is None:
                    warnings.add("external_mapping_target_invalid")
        if len(applicable) > 1:
            warnings.add("external_mapping_conflict")
        mapped = compatible_target(applicable[0].get("关联剧集"), theater, self.dramas) if len(applicable) == 1 else None
        if direct and mapped and direct != mapped:
            warnings.add("external_mapping_conflict")
        if grain != "drama":
            warnings.add("revenue_grain_unconfirmed")
        drama_id = (mapped or direct) if not warnings else None
        return RevenueResolution(
            drama_id,
            "confirmed" if drama_id else "ambiguous" if direct_value or applicable else "unmatched",
            tuple(sorted(mapping["record_id"] for mapping in applicable)),
            tuple(sorted(related.intersection(self.dramas))),
            tuple(sorted(warnings)),
        )
