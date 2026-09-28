"""Check the model's final prose against what this run's tools actually returned.

Ported in spirit from RealShort's ask answer-check / negative-claims: the model may explain,
but it cannot introduce titles no tool returned, claim a save that only the UI can commit,
or assert "not posted" unless a query filtered on publication records or the returned items'
own records back it. Findings are stored per answer and shown beside it; the answer itself is
never rewritten.
"""

import re
from bisect import bisect_right
from typing import NamedTuple

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
_INDENT = re.compile(r"[ \t]*")
# "都没发过" / "这三部" speak for every item returned, not for the title named last ("第2部" is one item).
_SUMMARY = re.compile(r"都|均|全部|其余|其他|以上|这些|这几|它们|(?<![第0-9一二两三四五六七八九十])[0-9一二两三四五六七八九十]+部")
# Two Latin words in a row the check cannot place are taken for a drama named without 《》: nothing returned speaks for
# it. One word is a theater, platform or language far more often than a drama (DramaBox, YouTube, en).
_LATIN_NAME = re.compile(r"[a-z][a-z0-9'’&]*(?:[ \t]+[a-z][a-z0-9'’&]*)+")
_NOT_NAME = re.compile(r"(?<![0-9a-z])(?:youtube|tiktok|facebook|instagram|fb|ig|yt|us|en|ko|ja|es|pt|th|zh)(?![0-9a-z'’&])")
# A clause about the whole team is not about the account a posted_account query cleared, even going on from one that is.
_TEAM_WORDS = re.compile(r"团队|全队|所有账号|任何账号|各个?账号|全部账号|哪个账号")
_TEAM = "\x00team"
# "这个账号" is the account a posted_account query asked about.
_THIS_ACCOUNT = re.compile(r"(?:该|这个|此|本)(?:账号|账户)")
# What a returned item's posted summary says about "没发过": only a matched record without posts backs it.
UNPOSTED, UNKNOWN, UNMATCHED, POSTED = "unposted", "unknown", "unmatched", "posted"
# Merging keeps the answer that refutes most: a title returned once without posts and once with posts was posted.
_SEVERITY = {UNPOSTED: 0, UNKNOWN: 1, UNMATCHED: 2, POSTED: 3}
_UNFILTERED = "本轮查询没有按发布记录过滤，不能据此断言没发过。"


class Seen(NamedTuple):
    """What the items returned with one normalized title say about "没发过"; accounts are casefolded."""

    title: str
    status: str
    # Accounts the records name as posting it, and accounts a posted_account query returned it for.
    posters: frozenset[str] = frozenset()
    clear: frozenset[str] = frozenset()


def _norm(title: str) -> str:
    return " ".join(title.casefold().split())


def _account(name) -> str:
    return str(name).strip().casefold()


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


def with_posted(seen: dict[str, Seen], items, *, account: str | None = None) -> dict[str, Seen]:
    """seen plus what these returned items' posted summaries say, by normalized title.

    account is the query's posted_account: every item it returned was not posted from that account. A title returned
    with different answers (two languages, say) keeps the one that refutes "没发过" most.
    """
    merged = dict(seen)
    cleared = frozenset({_account(account)}) if account and _account(account) else frozenset()
    for item in items:
        key, posted = _norm(item["title"]), item.get("posted")
        status = _posted_status(posted)
        posters = frozenset(_account(name) for name in posted.get("accounts") or ()) if isinstance(posted, dict) else frozenset()
        before = merged.get(key)
        if before is None:
            merged[key] = Seen(item["title"], status, posters, cleared)
        else:
            worse = max(before.status, status, key=_SEVERITY.__getitem__)
            merged[key] = Seen(before.title, worse, before.posters | posters, before.clear | cleared)
    return merged


class _Accounts:
    """The accounts this run's records name, and which of them a clause is about."""

    def __init__(self, seen: dict[str, Seen]) -> None:
        names = sorted({name for entry in seen.values() for name in entry.posters | entry.clear if name}, key=len, reverse=True)
        self.pattern = re.compile(r"(?<![0-9a-z])(?:" + "|".join(map(re.escape, names)) + r")(?![0-9a-z])") if names else None
        self.queried = frozenset(name for entry in seen.values() for name in entry.clear)

    def named(self, clause: str) -> tuple[frozenset[str], str]:
        """The accounts the clause names (_TEAM for the whole team), and its casefolded text with them blanked out."""
        folded, named = clause.casefold(), set()
        if self.pattern is not None:
            named.update(self.pattern.findall(folded))
            folded = self.pattern.sub("|", folded)
        if _THIS_ACCOUNT.search(clause):
            named |= self.queried
        if _TEAM_WORDS.search(clause):
            named.add(_TEAM)
        return frozenset(named), folded


class _Subject(NamedTuple):
    """What a claim is about: the titles named, whether an unplaced name is, whether it sums up all items; and from where."""

    titles: tuple[str, ...]
    unknown: bool
    summary: bool
    start: int
    # The indent of the line it starts on: a deeper line below is a detail of it.
    indent: int


def _own_subject(text: str, masked: str, accounts: _Accounts, left: int, right: int, indent: int) -> tuple[_Subject, frozenset[str]]:
    """The clause's own subject, and the accounts it names."""
    titles = tuple(dict.fromkeys(match.group(1).strip() for match in _TITLE.finditer(text, left, right) if match.group(1).strip()))
    named, words = accounts.named(masked[left:right])
    unknown = _LATIN_NAME.search(_NOT_NAME.sub("|", words)) is not None
    return _Subject(titles, unknown, _SUMMARY.search(masked, left, right) is not None, left, indent), named


def _subjects(text: str, masked: str, accounts: _Accounts) -> tuple[list[int], list[_Subject], list[frozenset[str]]]:
    """Each clause's start, its subject, and the accounts named from where that subject starts through the clause.

    A clause naming nothing that continues the one before takes that one's subject. A comma continues the clause before,
    and so does a line indented deeper than the line its subject starts on (a list item's details under its title). A
    sentence end, a semicolon, a blank line or a line at the same depth never does.
    """
    starts, subjects, mentions = [], [], []
    left, soft, indent = 0, False, _INDENT.match(masked).end()
    for mark in (*_CLAUSE_MARK.finditer(masked), None):
        own, named = _own_subject(text, masked, accounts, left, mark.start() if mark else len(masked), indent)
        borrows = soft and not (own.titles or own.unknown or own.summary)
        subjects.append(subjects[-1] if borrows else own)
        mentions.append((mentions[-1] | named if named else mentions[-1]) if borrows else named)
        starts.append(left)
        if mark is None:
            break
        left = mark.end()
        if mark.group() == "\n":
            indent = _INDENT.match(masked, left).end() - left
            soft = masked[left + indent : left + indent + 1] not in ("", "\n") and indent > subjects[-1].indent
        else:
            soft = mark.group() in "，,"
    return starts, subjects, mentions


def _listed(titles: dict[str, str]) -> str:
    return "、".join(f"《{title}》" for title in list(titles.values())[:5])


def _effective(entry: Seen, mentioned: frozenset[str]) -> str:
    """A claim naming only accounts a posted_account query cleared, and the records never name, is about those accounts.

    _TEAM is in no clear set: a claim that also names the team stays refuted by the team's posts.
    """
    if entry.status == POSTED and mentioned and mentioned <= entry.clear - entry.posters:
        return UNPOSTED
    return entry.status


class _Findings:
    """Across an answer's claims: the titles whose records refute one, and whether one stood on nothing."""

    def __init__(self) -> None:
        self.unmatched: dict[str, str] = {}
        self.posted: dict[str, str] = {}
        self.unfiltered = False

    def judge(self, entries: list[tuple[str, str]], *, unknown: bool, posted_checked: bool) -> None:
        """Unmatched and posted titles refute the claim; the rest back it only when all are matched without posts."""
        self.unmatched |= {_norm(title): title for title, status in entries if status == UNMATCHED}
        self.posted |= {_norm(title): title for title, status in entries if status == POSTED}
        if not posted_checked and (unknown or any(status == UNKNOWN for _, status in entries)):
            self.unfiltered = True

    def notes(self) -> list[str]:
        notes = []
        if self.unmatched:
            notes.append(_listed(self.unmatched) + "的发布记录没有对上，只能说“发布记录里没有”，不能说没发过。")
        if self.posted:
            notes.append("发布记录显示" + _listed(self.posted) + "发过，不能说没发过。")
        if self.unfiltered:
            notes.append(_UNFILTERED)
        return notes


def _not_posted_notes(text: str, posted_checked: bool, seen: dict[str, Seen]) -> list[str]:
    """Notes for "没发过" claims that nothing this run returned backs.

    A claim naming titles stands on their returned records, and one summing up ("都没发过") on every record returned:
    matched without posts backs it, unmatched or posted refutes it, and a title no tool returned needs the posted
    filter. A claim naming nothing needs the filter, and then every record returned must not refute it; without the
    filter, every item this run returned must be matched without posts.
    """
    # A title's inside is blanked out (same length): its words are no claim, and its punctuation ends no clause.
    masked = _TITLE.sub(lambda match: "《" + "_" * len(match.group(1)) + "》", text)
    starts, subjects, mentions = _subjects(text, masked, _Accounts(seen))
    findings, everything, judged = _Findings(), {}, set()
    for start in _claim_starts(_NOT_POSTED, masked):
        index = bisect_right(starts, start) - 1
        subject, mentioned = subjects[index], mentions[index]
        # A judgement depends only on what it reads: claims reading the same thing add nothing (and cost nothing).
        key = (subject.start, mentioned) if subject.titles else (None, mentioned, subject.unknown, subject.summary)
        if key in judged:
            continue
        judged.add(key)
        if subject.titles:
            missing = Seen("", UNKNOWN)
            entries = [(title, _effective(seen.get(_norm(title), missing), mentioned)) for title in subject.titles]
            findings.judge(entries, unknown=subject.unknown, posted_checked=posted_checked)
            continue
        if mentioned not in everything:
            everything[mentioned] = [(entry.title, _effective(entry, mentioned)) for entry in seen.values()]
        entries = everything[mentioned]
        if entries and (subject.summary or posted_checked):
            findings.judge(entries, unknown=subject.unknown, posted_checked=posted_checked)
        elif not posted_checked and (subject.unknown or not entries or any(status != UNPOSTED for _, status in entries)):
            findings.unfiltered = True
    return findings.notes()


def check_answer(text: str, *, known_titles: set[str], posted_checked: bool, posted_seen: dict[str, Seen] | None = None) -> list[str]:
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
