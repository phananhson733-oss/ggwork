"""Condition values checked against the batch before filtering: a value the batch does not contain is a mistake to
report, not a filter that silently matches nothing (2026-09-28: 「US 地区」 sent as theater, zero results three times)."""

import re
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
    **dict.fromkeys(("us", "usa", "u.s.", "u.s.a.", "america", "united states", "north america", "美国", "美國", "北美", "北美洲"), "en"),
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
# Words that only restate a filter. A query made of nothing but a region and these ("US 热门", "美国热门短剧") is a
# region and a hotness ask put in the title field; a query with anything else ("美國總裁") stays a title search.
FILTER_WORDS = (
    *("热门", "熱門", "爆款", "榜单", "榜單", "上榜", "地区", "地區", "市场", "市場", "最近", "最新", "短剧", "短劇", "剧", "劇", "的"),
    *("hot", "trending", "popular", "top", "drama", "dramas", "short", "shorts", "market", "region", "recent", "latest"),
    *("in", "the", "of", "for", "from", "and"),
)
# Region keys that are acronyms, longest first: a query holds them as a region only in capitals.
REGION_ACRONYMS = ("U.S.A.", "U.S.", "USA", "US", "UK")
_ACRONYM_KEYS = frozenset(acronym.casefold() for acronym in REGION_ACRONYMS)
# How many choices an error lists; the rest are summarised by count.
CHOICES_SHOWN = 30
# 换一批 merges the bound card's conditions under this call's: leaving a field out keeps the parent's value, so a
# refusal says how to clear the field it names.
_CLEAR = "要去掉这一项时显式传{field}:{empty}（换一批会沿用绑定候选的条件，省略不等于去掉）。"


def is_hot_kind(kind: str) -> bool:
    return kind in _HOT


def region_language(value: str) -> str | None:
    return REGION_LANGUAGES.get(value.strip().casefold())


def _word_pattern(word: str) -> str:
    # ASCII words must stand alone ("us" is not in "Husband"); CJK has no word boundaries to wait for.
    return rf"(?<![a-z0-9]){re.escape(word)}(?![a-z0-9])" if word.isascii() else re.escape(word)


def _strip_words(text: str, words) -> tuple[str, list[str]]:
    found = []
    for word in sorted(words, key=len, reverse=True):
        pattern = _word_pattern(word)
        if re.search(pattern, text):
            found = [*found, word]
            text = re.sub(pattern, " ", text)
    return text, found


def _languages(regions) -> tuple[str, ...]:
    return tuple(dict.fromkeys(REGION_LANGUAGES[region] for region in regions))


def query_languages(value: str) -> tuple[str, ...]:
    """The languages for a query that is only regions, or regions with filter words ("US 热门"), else ().

    Acronyms count only in capitals: "US" is the country, "Us" and "us" are title words ("For Us"). Inner whitespace
    is folded for the match only ("North  America")."""
    text, regions = " ".join(value.split()), []
    for acronym in REGION_ACRONYMS:
        pattern = rf"(?<![A-Za-z0-9]){re.escape(acronym)}(?![A-Za-z0-9])"
        if re.search(pattern, text):
            regions = [*regions, acronym.casefold()]
            text = re.sub(pattern, " ", text)
    text, named = _strip_words(text.casefold(), [key for key in REGION_LANGUAGES if key not in _ACRONYM_KEYS])
    if not regions and not named:
        return ()
    text, _ = _strip_words(text, FILTER_WORDS)
    return () if re.sub(r"[\W_]+", "", text) else _languages([*regions, *named])


def _languages_hint(label: str, languages: tuple[str, ...]) -> str:
    codes = "、".join(f"language={code}" for code in languages)
    if len(languages) == 1:
        return f"「{label}」是地区；剧库没有地区字段，请改用{codes}（按语种近似，回答里说明）。"
    if languages:
        return f"「{label}」含多个地区；剧库没有地区字段，按语种近似要分别查{codes}（回答里说明）。"
    return "剧库没有地区字段；按地区找剧请用语种代码（如美国/US用en）。"


def _region_hint(value: str) -> str:
    language = region_language(value)
    return _languages_hint(value, (language,) if language else query_languages(value))


def _names_a_title(rows, query: str) -> bool:
    # A title or tag that is exactly the region phrase ("In America") is what the user asked for, not a region.
    wanted = " ".join(query.casefold().split())
    return any(" ".join(row["title"].casefold().split()) == wanted or wanted in (t.casefold() for t in row["tags"]) for row in rows)


def _choices(counts: Counter) -> str:
    names = [name for name, _ in counts.most_common() if name]
    if not names:
        return "（当前批次没有可选值）"
    shown = "、".join(names[:CHOICES_SHOWN])
    return shown if len(names) <= CHOICES_SHOWN else f"{shown}等{len(names)}个"


def _clear(field: str, empty: str = "null") -> str:
    return _CLEAR.format(field=field, empty=empty)


def _check_theater(rows, theater: str) -> None:
    counts = Counter(row["theater"] for row in rows)
    if not any(name.casefold() == theater.casefold() for name in counts):
        raise ValueError(f"剧库里没有剧场「{theater}」；theater只填剧场名，可选：{_choices(counts)}。{_region_hint(theater)}{_clear('theater')}")


def _check_language(rows, language: str) -> None:
    counts = Counter(row["language"] for row in rows)
    if not any(code.casefold() == language.casefold() for code in counts):
        raise ValueError(f"剧库里没有语种「{language}」；language填语种代码，可选：{_choices(counts)}。{_region_hint(language)}{_clear('language')}")


def _check_tags(rows, tags: list[str]) -> None:
    counts = Counter(tag for row in rows for tag in row["tags"])
    missing = [tag for tag in tags if tag not in counts]
    if missing:
        if counts:
            hint = f"标签要与剧库原文完全一致，可选：{_choices(counts)}。query是剧名或标签原文的子串搜索，不做翻译。"
        else:
            hint = "当前批次的剧目都没有标签；query是剧名原文的子串搜索，不做翻译。"
        regions = [tag for tag in missing if region_language(tag)]
        if regions:
            hint += _languages_hint("、".join(regions), tuple(dict.fromkeys(region_language(tag) for tag in regions)))
        raise ValueError(f"剧库里没有标签「{'、'.join(missing)}」。{hint}{_clear('tags', '[]')}")


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
    if conditions.query and query_languages(conditions.query) and not _names_a_title(rows, conditions.query):
        raise ValueError(f"query是剧名或标签里的词，不是地区或热门条件。{_region_hint(conditions.query)}要热门依据用hot_only。{_clear('query')}")
    if conditions.signal_kind and not any(s["kind"] == conditions.signal_kind for row in rows for s in row["signals"]):
        rank = "按名次排序的要一并传sort:evidence_date。" if conditions.sort == "rank" else ""
        raise ValueError(f"剧库里没有 {conditions.signal_kind} 这类信号；信号种类代码见知识资料。{_clear('signal_kind')}{rank}")
    if conditions.hot_only and not any(is_hot_kind(s["kind"]) for row in rows for s in row["signals"]):
        raise ValueError(f"剧库里没有热门依据类信号（剧场榜单、评级、剧单或备注）；去掉hot_only，或用signal_kind指定依据。{_clear('hot_only', 'false')}")


def hot_scope(rows) -> dict:
    """Which of the batch's signal kinds hot_only counted and which it did not, for the model to explain."""
    present = {s["kind"] for row in rows for s in row["signals"]}
    return {"counted": [kind for kind in HOT_SIGNAL_KINDS if kind in present], "not_counted": sorted(present - _HOT)}
