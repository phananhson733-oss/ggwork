"""Check the model's final prose against what this run's tools actually returned.

Ported in spirit from RealShort's ask answer-check / negative-claims: the model may explain,
but it cannot introduce titles no tool returned, claim a save that only the UI can commit,
or assert "not posted" unless a query filtered on publication records or the returned items'
own records back it. Findings are stored per answer and shown beside it; the answer itself is
never rewritten.
"""

import re
from bisect import bisect_right

_TITLE = re.compile(r"《([^《》\n]{1,500})》")
_WHO = r"(为你|帮你|给你)?(成功)?"
# "已保存的剧" describes earlier saves and "已加入…条件" edits a filter; only a save into the list is a claim.
_SAVE_CLAIM = re.compile(
    rf"(已经?|成功){_WHO}(保存|收藏|存入)(?!的|过的)"
    rf"|(已经?|成功){_WHO}(加入|写入|添加|放入)(到|进)?(了)?(你的)?(个人)?(选剧)?清单"
    r"|保存(成功|好了|完成)(?!后)"
)
# 发 must be the verb: 没发现/没发生/没有发布记录 are not claims, and "已排期未发" relays a card warning.
_NOT_POSTED = re.compile(r"(?<!排期)(从来没有?|从没|没有?|未曾?)(被)?(发布|发(?!布))(过)?(?!现|生|展|放|起|出|送|给|挥|记录)")
# A claim quoted inside a disclaimer ("不能声称没发过") is not a claim; a comma ends the disclaimer.
_NEGATING_PREFIX = re.compile(r"(不能|无法|不代表|不等于|不能声称|不能断言|不能确认|不会|才会|是否|请勿|不要)[^。！？，,；;\n]{0,6}$")
# Longer than any disclaimer _NEGATING_PREFIX reads, so a claim looks back this far instead of through the whole answer.
_PREFIX_WINDOW = 16
_CLAUSE_MARK = re.compile(r"[。！？!?；;，,\n]")
_BLANK_LINE = re.compile(r"\n[ \t]*\n")
# What a returned item's posted summary says about "没发过": only a matched record without posts backs it.
UNPOSTED, POSTED, UNMATCHED, UNKNOWN = "unposted", "posted", "unmatched", "unknown"
_UNFILTERED = "本轮查询没有按发布记录过滤，不能据此断言没发过。"


def _norm(title: str) -> str:
    return " ".join(title.casefold().split())


def _claim_starts(pattern: re.Pattern, text: str) -> list[int]:
    return [match.start() for match in pattern.finditer(text) if not _NEGATING_PREFIX.search(text, max(0, match.start() - _PREFIX_WINDOW), match.start())]


def _claims(pattern: re.Pattern, text: str) -> bool:
    return bool(_claim_starts(pattern, text))


def titles_in(text: str) -> set[str]:
    return {match.group(1).strip() for match in _TITLE.finditer(text) if match.group(1).strip()}


def _posted_status(posted) -> str:
    if not isinstance(posted, dict):
        return UNKNOWN
    if posted.get("matched") is not True:
        return UNMATCHED
    return UNPOSTED if posted.get("post_count") == 0 else POSTED


def with_posted(seen: dict[str, str], items) -> dict[str, str]:
    """seen plus what these returned items' posted summaries say, by normalized title.

    A title returned with two different answers (two languages, say) backs nothing.
    """
    merged = dict(seen)
    for item in items:
        title, status = _norm(item["title"]), _posted_status(item.get("posted"))
        merged[title] = status if merged.get(title, status) == status else UNKNOWN
    return merged


def _clause_subjects(text: str, masked: str) -> tuple[list[int], list[list[str]]]:
    """Each clause's start and the titles a claim in it is about, in one pass.

    A clause's own titles, else those of the nearest earlier clause with titles in its paragraph (a list item's title
    on the line above its verdict). A title named without 《》 is not seen, so its claim borrows the clause before.
    """
    paragraphs = [0, *(match.end() for match in _BLANK_LINE.finditer(masked))]
    starts = [0, *(match.end() for match in _CLAUSE_MARK.finditer(masked))]
    subjects: list[list[str]] = []
    previous, previous_paragraph = [], -1
    for left, right in zip(starts, [*starts[1:], len(masked)]):
        paragraph = bisect_right(paragraphs, left)
        own = list(dict.fromkeys(match.group(1).strip() for match in _TITLE.finditer(text, left, right) if match.group(1).strip()))
        previous = own or (previous if paragraph == previous_paragraph else [])
        previous_paragraph = paragraph
        subjects.append(previous)
    return starts, subjects


def _listed(titles: dict[str, str]) -> str:
    return "、".join(f"《{title}》" for title in list(titles.values())[:5])


def _not_posted_notes(text: str, posted_checked: bool, seen: dict[str, str]) -> list[str]:
    """Notes for "没发过" claims that nothing this run returned backs.

    A claim naming titles stands on their returned records: matched without posts backs it, unmatched or posted
    refutes it, and a title no tool returned needs the posted filter. A claim naming none needs the filter, or every
    item this run returned to be matched without posts.
    """
    # A title's inside is blanked out (same length): its words are no claim, and its punctuation ends no clause.
    masked = _TITLE.sub(lambda match: "《" + "_" * len(match.group(1)) + "》", text)
    starts, subjects = _clause_subjects(text, masked)
    unmatched, posted, unfiltered = {}, {}, False
    for start in _claim_starts(_NOT_POSTED, masked):
        titles = subjects[bisect_right(starts, start) - 1]
        statuses = {title: seen.get(_norm(title), UNKNOWN) for title in titles}
        unmatched |= {_norm(title): title for title, status in statuses.items() if status == UNMATCHED}
        posted |= {_norm(title): title for title, status in statuses.items() if status == POSTED}
        if posted_checked or {UNMATCHED, POSTED} & set(statuses.values()):
            continue
        backing = list(statuses.values()) if titles else list(seen.values())
        if not backing or any(status != UNPOSTED for status in backing):
            unfiltered = True
    notes = []
    if unmatched:
        notes.append(_listed(unmatched) + "的发布记录没有对上，只能说“发布记录里没有”，不能说没发过。")
    if posted:
        notes.append("发布记录显示" + _listed(posted) + "发过，不能说没发过。")
    if unfiltered:
        notes.append(_UNFILTERED)
    return notes


def check_answer(text: str, *, known_titles: set[str], posted_checked: bool, posted_seen: dict[str, str] | None = None) -> list[str]:
    notes = []
    known = {_norm(title) for title in known_titles}
    unknown = []
    for match in _TITLE.finditer(text):
        title = match.group(1).strip()
        if title and _norm(title) not in known and title not in unknown:
            unknown.append(title)
    if unknown:
        notes.append("正文提到的" + "、".join(f"《{t}》" for t in unknown[:5]) + "不在本轮查询结果中，请以候选卡为准。")
    if _claims(_SAVE_CLAIM, text):
        notes.append("本轮没有写入个人清单；只有点击「确认保存」并看到回执才算保存。")
    notes.extend(_not_posted_notes(text, posted_checked, posted_seen or {}))
    return notes
