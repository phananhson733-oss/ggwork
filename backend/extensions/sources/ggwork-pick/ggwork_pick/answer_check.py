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
_SUMMARY = re.compile(r"都|均|全部|这些|这几|它们|(?<![第0-9一二两三四五六七八九十])[0-9一二两三四五六七八九十]+部")
# "以上两部" sums up the titles named before it as well ("100集以上" is a count); "其余" every title but those.
_ABOVE = re.compile(r"(?<![0-9一二两三四五六七八九十百千万%％集部岁分秒天周月年次条个])以上|上述")
_REST = re.compile(r"(?:其余|其他|其它|剩下|剩余|余下)(?!的?(?:账号|账户))")
# "除了《A》都没发过" / "《A》以外其余都没发过" sum up every title but the ones the clause names.
_EXCEPT = re.compile(r"除了|除去|除开|》(?:以外|之外)")
ALL, ABOVE, REST = "all", "above", "rest"
# Latin words: a title this run returned may be written without 《》. Two other words in a row are taken for a drama
# named without 《》 that nothing returned speaks for; one word is a theater, platform or language far more often than a
# drama (DramaBox, YouTube, en). A run of words breaks at anything but spaces and tabs; one pass reads them all.
_LATIN_TOKEN = re.compile(r"[a-z0-9'’&]+")
_LETTER = re.compile(r"[a-z]")
_NOT_NAMES = frozenset({"youtube", "tiktok", "facebook", "instagram", "fb", "ig", "yt", "us", "en", "ko", "ja", "es", "pt", "th", "zh"})
# A clause about the whole team, or the accounts besides one, is not about the account a posted_account query cleared.
_TEAM_WORDS = re.compile(r"团队|全队|所有账号|任何账号|各个?账号|全部账号|哪个账号|(?:其他|其余|其它|别的)的?账号")
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


def _latin_runs(folded: str) -> list[list[str]]:
    """The runs of Latin tokens (letters, digits, apostrophes, &) with nothing but spaces and tabs between them."""
    runs: list[list[str]] = []
    end = None
    for match in _LATIN_TOKEN.finditer(folded):
        if end is None or folded[end : match.start()].strip(" \t"):
            runs.append([])
        runs[-1].append(match.group())
        end = match.end()
    return runs


class _BareTitles:
    """The titles this run returned that are all Latin words, to find them written without 《》."""

    def __init__(self, seen: dict[str, Seen]) -> None:
        self.titles: dict[tuple[str, ...], str] = {}
        # Each first word's title lengths, longest first: "lost heir 2" wins over "lost heir".
        self.lengths: dict[str, list[int]] = {}
        for key, entry in seen.items():
            words = tuple(_LATIN_TOKEN.findall(key))
            if words and " ".join(words) == key and words not in self.titles:
                self.titles[words] = entry.title
                self.lengths.setdefault(words[0], []).append(len(words))
        for lengths in self.lengths.values():
            lengths.sort(reverse=True)

    def _title_at(self, run: list[str], index: int) -> tuple[str, int] | None:
        for length in self.lengths.get(run[index], ()):
            title = self.titles.get(tuple(run[index : index + length])) if index + length <= len(run) else None
            if title is not None:
                return title, length
        return None

    def scan(self, folded: str) -> tuple[list[str], bool]:
        """The returned titles folded text names bare, and whether two other words in a row name something unplaced."""
        found, unknown = [], False
        for run in _latin_runs(folded):
            index, word_before = 0, False
            while index < len(run):
                hit = self._title_at(run, index)
                if hit is not None:
                    found.append(hit[0])
                    index, word_before = index + hit[1], False
                    continue
                word = run[index] not in _NOT_NAMES and _LETTER.search(run[index]) is not None
                unknown = unknown or (word and word_before)
                index, word_before = index + 1, word
        return found, unknown


class _Subject(NamedTuple):
    """What a claim is about: the titles named, whether an unplaced name is, what it sums up (None, ALL, ABOVE, REST);
    and from where."""

    titles: tuple[str, ...]
    unknown: bool
    summary: str | None
    start: int
    # The indent of the line it starts on: a deeper line below is a detail of it.
    indent: int


def _summary(masked: str, left: int, right: int) -> str | None:
    if _REST.search(masked, left, right):
        return REST
    if _ABOVE.search(masked, left, right):
        return ABOVE
    return ALL if _SUMMARY.search(masked, left, right) else None


def _own_subject(
    text: str, masked: str, clause: tuple[_Accounts, _BareTitles], left: int, right: int, indent: int
) -> tuple[_Subject, frozenset[str], tuple[str, ...]]:
    """The clause's own subject, the accounts it names, and the titles it names."""
    accounts, bare = clause
    named, words = accounts.named(masked[left:right])
    found, unknown = bare.scan(words)
    bracketed = (match.group(1).strip() for match in _TITLE.finditer(text, left, right))
    titles = tuple(dict.fromkeys(title for title in (*bracketed, *found) if title))
    summary = _summary(masked, left, right)
    if summary is not None and _EXCEPT.search(masked, left, right):
        return _Subject((), unknown, REST, left, indent), named, titles
    return _Subject(titles, unknown, summary, left, indent), named, titles


class _Clauses(NamedTuple):
    starts: list[int]
    subjects: list[_Subject]
    # The accounts a claim in the clause is about.
    mentions: list[frozenset[str]]
    # The titles each clause names itself, bracketed or bare.
    owns: list[tuple[str, ...]]


def _clauses(text: str, masked: str, seen: dict[str, Seen]) -> _Clauses:
    """Each clause's start, its subject, the accounts it is about, and the titles it names itself.

    A clause naming nothing that continues the one before takes that one's subject. A comma continues the clause before,
    and so does a line indented deeper than the line its subject starts on (a list item's details under its title). A
    sentence end, a semicolon, a blank line or a line at the same depth never does. A clause naming an account is about
    that account; one naming none that continues the one before is about the accounts that one is about.
    """
    found = _Clauses([], [], [], [])
    reading = (_Accounts(seen), _BareTitles(seen))
    left, soft, indent = 0, False, _INDENT.match(masked).end()
    for mark in (*_CLAUSE_MARK.finditer(masked), None):
        own, named, titles = _own_subject(text, masked, reading, left, mark.start() if mark else len(masked), indent)
        borrows = soft and not (own.titles or own.unknown or own.summary)
        found.subjects.append(found.subjects[-1] if borrows else own)
        found.mentions.append((named or found.mentions[-1]) if borrows else named)
        found.owns.append(titles)
        found.starts.append(left)
        if mark is None:
            break
        left = mark.end()
        if mark.group() == "\n":
            indent = _INDENT.match(masked, left).end() - left
            soft = masked[left + indent : left + indent + 1] not in ("", "\n") and indent > found.subjects[-1].indent
        else:
            soft = mark.group() in "，,"
    return found


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


class _Judge:
    """Judges an answer's claims in order. Judging only adds, so a claim reading nothing new adds nothing (and costs
    nothing): the first "其余" reads the most, and "以上" reads only the titles named since the last one."""

    def __init__(self, seen: dict[str, Seen], posted_checked: bool, owns: list[tuple[str, ...]]) -> None:
        self.seen, self.posted_checked, self.owns = seen, posted_checked, owns
        self.findings = _Findings()
        # The titles named in the clauses before self.through, in order.
        self.named: set[str] = set()
        self.order: list[str] = []
        self.through = 0
        self.done: set[tuple] = set()
        self.above: dict[tuple, int] = {}

    def _name_through(self, index: int) -> None:
        for titles in self.owns[self.through : index + 1]:
            for title in titles:
                if _norm(title) not in self.named:
                    self.named.add(_norm(title))
                    self.order.append(title)
        self.through = max(self.through, index + 1)

    def _entries(self, titles, mentioned: frozenset[str]) -> list[tuple[str, str]]:
        missing = Seen("", UNKNOWN)
        return [(title, _effective(self.seen.get(_norm(title), missing), mentioned)) for title in titles]

    def _stand(self, entries: list[tuple[str, str]], subject: _Subject) -> None:
        """A summing-up claim: the entries it sums up judge it; with none, only the posted filter backs it."""
        if entries:
            self.findings.judge(entries, unknown=subject.unknown, posted_checked=self.posted_checked)
        elif not self.posted_checked:
            self.findings.unfiltered = True

    def claim(self, subject: _Subject, mentioned: frozenset[str], index: int) -> None:
        self._name_through(index)
        if subject.summary == REST and not subject.titles:
            key = (REST, mentioned, subject.unknown)
            if key not in self.done:
                self.done.add(key)
                self._stand([(entry.title, _effective(entry, mentioned)) for norm, entry in self.seen.items() if norm not in self.named], subject)
        elif subject.summary == ABOVE and self.order:
            key = (ABOVE, mentioned, subject.unknown)
            begin = self.above.get(key, 0)
            if begin < len(self.order):
                self._stand(self._entries(self.order[begin:], mentioned), subject)
                self.above[key] = len(self.order)
        elif subject.titles:
            key = (subject.start, mentioned)
            if key not in self.done:
                self.done.add(key)
                self.findings.judge(self._entries(subject.titles, mentioned), unknown=subject.unknown, posted_checked=self.posted_checked)
        else:
            self._everything(subject, mentioned)

    def _everything(self, subject: _Subject, mentioned: frozenset[str]) -> None:
        key = (None, mentioned, subject.unknown, subject.summary is not None)
        if key in self.done:
            return
        self.done.add(key)
        entries = [(entry.title, _effective(entry, mentioned)) for entry in self.seen.values()]
        if subject.summary is not None or self.posted_checked:
            self._stand(entries, subject)
        elif subject.unknown or not entries or any(status != UNPOSTED for _, status in entries):
            self.findings.unfiltered = True


def _not_posted_notes(text: str, posted_checked: bool, seen: dict[str, Seen]) -> list[str]:
    """Notes for "没发过" claims that nothing this run returned backs.

    A claim naming titles stands on their returned records: matched without posts backs it, unmatched or posted refutes
    it, and a title no tool returned needs the posted filter. One summing up stands on the records it sums up: "以上"
    on every title named up to it, "其余" on every record returned but those titles', "都没发过" on every record
    returned. A claim naming nothing needs the filter, and then every record returned must not refute it; without the
    filter, every item this run returned must be matched without posts.
    """
    # A title's inside is blanked out (same length): its words are no claim, and its punctuation ends no clause.
    masked = _TITLE.sub(lambda match: "《" + "_" * len(match.group(1)) + "》", text)
    claims = _claim_starts(_NOT_POSTED, masked)
    if not claims:
        return []
    clauses = _clauses(text, masked, seen)
    judge = _Judge(seen, posted_checked, clauses.owns)
    for start in claims:
        index = bisect_right(clauses.starts, start) - 1
        judge.claim(clauses.subjects[index], clauses.mentions[index], index)
    return judge.findings.notes()


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
