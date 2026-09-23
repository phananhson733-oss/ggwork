"""Check the model's final prose against what this run's tools actually returned.

Ported in spirit from RealShort's ask answer-check / negative-claims: the model may explain,
but it cannot introduce titles no tool returned, claim a save that only the UI can commit,
or assert "not posted" without a query that filtered on publication records. Findings are
appended as a visible note instead of rewriting the answer.
"""

import re

_TITLE = re.compile(r"《([^《》\n]{1,200})》")
_SAVE_CLAIM = re.compile(r"(已经?|成功)(为你|帮你|给你)?(成功)?(保存|加入|写入|添加)|保存(成功|好了|完成)")
_NOT_POSTED = re.compile(r"(从来没有?|从没|没有?|未曾?)(被)?(发布|发)(过)?")
# A claim quoted inside a disclaimer ("不能声称没发过") is not a claim.
_NEGATING_PREFIX = re.compile(r"(不能|无法|不代表|不等于|不能声称|不能断言|不能确认|不会|才会|是否|尚未|请勿|不要)[^。！？\n]{0,8}$")
NOTE_HEADER = "核对提示"


def _norm(title: str) -> str:
    return " ".join(title.casefold().split())


def _claims(pattern: re.Pattern, text: str) -> bool:
    for match in pattern.finditer(text):
        if not _NEGATING_PREFIX.search(text[: match.start()]):
            return True
    return False


def titles_in(text: str) -> set[str]:
    return {match.group(1).strip() for match in _TITLE.finditer(text) if match.group(1).strip()}


def check_answer(text: str, *, known_titles: set[str], posted_checked: bool) -> list[str]:
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
    if not posted_checked and _claims(_NOT_POSTED, text):
        notes.append("本轮查询没有按发布记录过滤，不能据此断言没发过。")
    return notes


def append_notes(text: str, notes: list[str]) -> str:
    if not notes:
        return text
    return text.rstrip() + "\n\n> " + NOTE_HEADER + "：" + " ".join(notes)
