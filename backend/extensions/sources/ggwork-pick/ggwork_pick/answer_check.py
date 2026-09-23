"""Check the model's final prose against what this run's tools actually returned.

Ported in spirit from RealShort's ask answer-check / negative-claims: the model may explain,
but it cannot introduce titles no tool returned, claim a save that only the UI can commit,
or assert "not posted" without a query that filtered on publication records. Findings are
stored per answer and shown beside it; the answer itself is never rewritten.
"""

import re

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
