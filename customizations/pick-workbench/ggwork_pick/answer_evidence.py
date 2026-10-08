"""Typed run evidence from owner-checked tools, before model-only compression.

Imported prose is data, never an assertion to certify. Only typed numeric/date/rule
values enter assertion templates. Unsupported source fields remain unknown.
"""

import json
import math
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal
from urllib.parse import urlsplit


@dataclass(frozen=True)
class EvidenceAtom:
    prefix: str
    value: str | None
    suffix: str
    reference: str
    subject_id: str | None = None
    field_name: str | None = None
    unit: str | None = None
    observed_at: str | None = None
    source_ref: str | None = None
    display_prefix: str | None = None
    display_suffix: str | None = None

    @property
    def claim(self) -> str | None:
        return self.prefix + self.value + self.suffix if self.value is not None else None

    @property
    def display_claim(self) -> str | None:
        if self.value is None:
            return None
        return (self.display_prefix or self.prefix) + self.value + (self.display_suffix if self.display_suffix is not None else self.suffix)


@dataclass(frozen=True)
class EvidenceRead:
    tool: str
    call_id: str
    status: Literal["success", "unavailable"]
    catalog_batch_id: str | None
    result_id: str | None
    item_ids: tuple[str, ...]
    conditions_json: str
    data_as_of_json: str


def _number(value) -> str | None:
    if type(value) is int or (type(value) is float and math.isfinite(value)):
        return str(value)
    # Numeric text is common in imported feeds. Text with a unit or prose is not a scalar.
    if isinstance(value, str) and re.fullmatch(r"-?\d+(?:\.\d+)?", value, flags=re.ASCII):
        return value
    return None


def _date(value) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return value


def _label(value) -> bool:
    # Titles/names cannot break their delimiters or inject another clause. Unusual
    # source labels can still appear on cards; this narrow checker leaves them unknown.
    return isinstance(value, str) and bool(re.fullmatch(r"[\w '\-]+", value))


def _url(value) -> bool:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9:/?&=._~%+#@!$()*+,;\-]+", value):
        return False
    parsed = urlsplit(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.hostname) and not parsed.username


# Stable source kinds used by the existing rank sorter; labels never come from imported prose.
_RANK_LABELS = {"kd": "KalosTV日榜", "qc": "鹊娱7日转化率Top25", "qr": "鹊娱7日总收入Top25"}


@dataclass
class AnswerEvidence:
    atoms: list[EvidenceAtom] = field(default_factory=list)
    titles: set[str] = field(default_factory=set)
    reads: list[EvidenceRead] = field(default_factory=list)

    def capture(self, tool: str, call_id: str, payload: dict) -> None:
        if tool not in {"pick_query_candidates", "pick_count_candidates", "pick_get_drama_detail"}:
            return
        reference = f"tool:{call_id}"
        success = payload.get("status") in (None, "ok")
        items = payload.get("items", [payload["item"]] if "item" in payload else []) if success else []
        self.reads.append(
            EvidenceRead(
                tool,
                call_id,
                "success" if success else "unavailable",
                payload.get("catalog_batch_id"),
                payload.get("id", payload.get("result_id")),
                tuple(item["item_id"] for item in items),
                json.dumps(payload.get("conditions"), ensure_ascii=False, sort_keys=True),
                json.dumps(payload.get("data_as_of"), ensure_ascii=False, sort_keys=True),
            )
        )
        if tool in {"pick_query_candidates", "pick_count_candidates"}:
            total = payload.get("matched_total", payload.get("total")) if success else None
            self.atoms.append(
                EvidenceAtom(
                    "本次查询符合条件总数为",
                    str(total) if type(total) is int and total >= 0 else None,
                    "部",
                    reference,
                    field_name="matched_total",
                    unit="部",
                    display_prefix="本次查询共",
                )
            )
        for item in items:
            self.titles.add(item["title"])
            if not _label(item["title"]):
                continue
            title = f"《{item['title']}》"
            subject = item.get("identity", item["item_id"])
            item_ref = f"result:{payload.get('id', payload.get('result_id', call_id))}:{item['item_id']}"
            listed = _date(item.get("listed_at"))
            if listed:
                self.atoms.append(EvidenceAtom(f"{title}的上架日期为", listed, "", item_ref, subject, "listed_at"))
            language = item.get("language", "")
            if re.fullmatch(r"[a-z]{2,3}(?:-[A-Za-z0-9]{2,8})*", language):
                self.atoms.append(EvidenceAtom(f"{title}的语种为", language, "", item_ref, subject, "language"))
            if _label(item.get("theater")):
                self.atoms.append(EvidenceAtom(f"{title}的剧场为“", item["theater"], "”", item_ref, subject, "theater"))
            for channel, rule in item.get("channel_rules", {}).items():
                if channel not in {"youtube", "tiktok", "facebook"}:
                    continue
                self.atoms.append(
                    EvidenceAtom(
                        f"{title}的{channel}规则为",
                        {"allowed": "允许", "denied": "禁止"}.get(rule),
                        "",
                        item_ref,
                        subject,
                        f"channel_rules.{channel}",
                    )
                )
            for signal in item.get("evidence", []):
                citation, kind = signal["citation_id"], signal["kind"]
                if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,99}", kind):
                    continue
                observed = _date(signal.get("observed_at"))
                grade = signal.get("grade", "")
                fields = (
                    ("value", "", _number(signal.get("value"))),
                    ("rank", "名次", _number(signal.get("rank"))),
                    ("observed_at", "观测日期", observed),
                    ("grade", "等级", grade if re.fullmatch(r"[A-Z][+-]?", grade) else None),
                )
                for key, label, value in fields:
                    prefix, suffix, unit = f"{title}的{kind}{label}为", "", None
                    display_prefix, display_suffix = f"{title}的来源字段“{kind}{label}”为", ""
                    if kind == "episodes" and key == "value":
                        display_prefix, display_suffix, unit = f"{title}共", "集", "集"
                    elif kind in _RANK_LABELS and key == "rank" and observed:
                        display_prefix, display_suffix = f"{title}在{observed}的{_RANK_LABELS[kind]}第", "名"
                    self.atoms.append(
                        EvidenceAtom(
                            prefix,
                            value,
                            suffix,
                            citation,
                            subject,
                            f"{kind}.{key}",
                            unit,
                            observed,
                            signal.get("source_ref"),
                            display_prefix,
                            display_suffix,
                        )
                    )
                if _url(signal.get("source_ref")):
                    self.atoms.append(EvidenceAtom(f"来源[{citation}]为", signal["source_ref"], "", citation))
