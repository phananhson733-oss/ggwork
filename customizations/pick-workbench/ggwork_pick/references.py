"""Condition values checked against the batch before filtering: a value the batch does not contain is a mistake to
report, not a filter that silently matches nothing (2026-09-28: 「US 地区」 sent as theater, zero results three times)."""

from collections import Counter

from ggwork_pick.contracts import PickConditions

# Signal kinds that count as 热门依据 for hot_only: the theater side's boards, ratings, lists and notes, in the data
# page's THEATER_BASES order (frontend core/pick-board/request.ts; test_hot_kinds_are_the_data_page_theater_bases keeps
# them equal). A whitelist: ReelShort's site-derived kinds (clk, bill, gsc), observation kinds and any kind added later
# count only once listed here.
HOT_SIGNAL_KINDS = ("kd", "kw", "qc", "qr", "sm", "smd", "mg", "fh", "sh", "gh", "gn", "ghh", "dbn")
_HOT = frozenset(HOT_SIGNAL_KINDS)

# The catalog has no region field, only language. A region written where a theater, language, tag or title word goes
# is refused with the language to use instead. Keys are casefolded; a hint list, not a filter.
REGION_LANGUAGES = {
    **dict.fromkeys(("us", "usa", "u.s.", "u.s.a.", "america", "united states", "美国", "美國", "北美", "北美洲"), "en"),
    **dict.fromkeys(("uk", "united kingdom", "britain", "英国", "英國", "欧美", "歐美", "英美"), "en"),
    **dict.fromkeys(("korea", "south korea", "韩国", "韓國"), "ko"),
    **dict.fromkeys(("japan", "日本"), "ja"),
    **dict.fromkeys(("spain", "mexico", "latam", "latin america", "西班牙", "墨西哥", "拉美"), "es"),
    **dict.fromkeys(("brazil", "portugal", "巴西", "葡萄牙"), "pt"),
    **dict.fromkeys(("indonesia", "印尼", "印度尼西亚"), "id"),
    **dict.fromkeys(("thailand", "泰国", "泰國"), "th"),
    **dict.fromkeys(("france", "法国", "法國"), "fr"),
    **dict.fromkeys(("germany", "德国", "德國"), "de"),
    **dict.fromkeys(("taiwan", "hong kong", "台湾", "台灣", "香港", "港台"), "zh-hant"),
    **dict.fromkeys(("middle east", "saudi arabia", "中东", "中東", "沙特"), "ar"),
}
# How many choices an error lists; the rest are summarised by count.
CHOICES_SHOWN = 30


def is_hot_kind(kind: str) -> bool:
    return kind in _HOT


def region_language(value: str) -> str | None:
    return REGION_LANGUAGES.get(value.strip().casefold())


def _region_hint(value: str) -> str:
    language = region_language(value)
    if language:
        return f"「{value}」是地区；剧库没有地区字段，请改用language={language}（按语种近似，回答里说明）。"
    return "剧库没有地区字段；按地区找剧请用语种代码（如美国/US用en）。"


def _choices(counts: Counter) -> str:
    names = [name for name, _ in counts.most_common() if name]
    if not names:
        return "（当前批次没有可选值）"
    shown = "、".join(names[:CHOICES_SHOWN])
    return shown if len(names) <= CHOICES_SHOWN else f"{shown}等{len(names)}个"


def _check_theater(rows, theater: str) -> None:
    counts = Counter(row["theater"] for row in rows)
    if not any(name.casefold() == theater.casefold() for name in counts):
        raise ValueError(f"剧库里没有剧场「{theater}」；theater只填剧场名，可选：{_choices(counts)}。{_region_hint(theater)}")


def _check_language(rows, language: str) -> None:
    counts = Counter(row["language"] for row in rows)
    if not any(code.casefold() == language.casefold() for code in counts):
        raise ValueError(f"剧库里没有语种「{language}」；language填语种代码，可选：{_choices(counts)}。{_region_hint(language)}")


def _check_tags(rows, tags: list[str]) -> None:
    known = {tag for row in rows for tag in row["tags"]}
    missing = [tag for tag in tags if tag not in known]
    if missing:
        regions = [tag for tag in missing if region_language(tag)]
        hint = _region_hint(regions[0]) if regions else "标签要与剧库原文一致；不确定时改用query搜剧名或标签。"
        raise ValueError(f"剧库里没有标签「{'、'.join(missing)}」。{hint}")


def check_references(rows, conditions: PickConditions) -> None:
    """Refuse a theater, language, tag, signal kind or account the batch does not contain, a region used as a title
    word, and hot_only on a batch without hot evidence. Only for a new query or count: a stored result is replayed
    without it, on the batch it already passed."""
    if conditions.theater:
        _check_theater(rows, conditions.theater)
    if conditions.language:
        _check_language(rows, conditions.language)
    if conditions.tags:
        _check_tags(rows, conditions.tags)
    if conditions.query and region_language(conditions.query):
        raise ValueError(f"query是剧名或标签里的词，不是地区。{_region_hint(conditions.query)}")
    if conditions.signal_kind and not any(s["kind"] == conditions.signal_kind for row in rows for s in row["signals"]):
        raise ValueError(f"剧库里没有 {conditions.signal_kind} 这类信号；信号种类代码见知识资料")
    if conditions.hot_only and not any(is_hot_kind(s["kind"]) for row in rows for s in row["signals"]):
        raise ValueError("剧库里没有热门依据类信号（剧场榜单、评级、剧单或备注）；去掉hot_only，或用signal_kind指定依据")


def hot_scope(rows) -> dict:
    """Which of the batch's signal kinds hot_only counted and which it did not, for the model to explain."""
    present = {s["kind"] for row in rows for s in row["signals"]}
    return {"counted": [kind for kind in HOT_SIGNAL_KINDS if kind in present], "not_counted": sorted(present - _HOT)}
