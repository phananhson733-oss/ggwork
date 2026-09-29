"""Check the model's final prose against what this run's tools actually returned.

Ported in spirit from RealShort's ask answer-check / negative-claims: the model may explain,
but it cannot introduce titles no tool returned, claim a save that only the UI can commit,
or assert "not posted" unless a query filtered on publication records or the returned items'
own records back it. Findings are stored per answer and shown beside it; the answer itself is
never rewritten.
"""

import re
import sys
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
# 发 must be the verb: 没发现/没发生/没有发布记录 are not claims, and "已排期未发" relays a card warning. Where or by whom
# may stand between the negation and the verb ("没在 B 账号发过", "没有被团队发布过"), within the clause and before any
# other 发, so "没在清单里发现" stays no claim.
_NOT_POSTED = re.compile(
    r"(?<!排期)(?:从来没有?|从没有?|没有?|未曾?|不曾|尚无|暂无|从无|并无)(?:[在被][^。！？!?；;，,\n发]{0,16}?)?"
    r"(?:发布|发(?!布))过?(?!现|生|展|放|起|出|送|给|挥|记录)"
)
# A claim quoted inside a disclaimer ("不能声称没发过") is not a claim; a comma ends the disclaimer.
_NEGATING_PREFIX = re.compile(r"(不能|无法|不代表|不等于|不能声称|不能断言|不能确认|不会|才会|是否|请勿|不要)[^。！？，,；;\n]{0,6}$")
# Longer than any disclaimer _NEGATING_PREFIX reads, so a claim looks back this far instead of through the whole answer.
_PREFIX_WINDOW = 16
_CLAUSE_MARK = re.compile(r"[。！？!?；;，,\n]")
# The marks after a clause that only lists titles which carry the list on to the next clause.
_LIST_MARKS = frozenset("，,；;\n")
_INDENT = re.compile(r"[ \t]*")
# "都没发过" / "这三部" speak for every item returned, not for the title named last ("第2部" is one item).
_SUMMARY = re.compile(r"都|均|全部|这些|这几|它们|(?<![第0-9一二两三四五六七八九十])[0-9一二两三四五六七八九十]+部")
# "以上两部" sums up the titles named before it as well ("100集以上" is a count); "其余" every title but those.
_ABOVE = re.compile(r"(?<![0-9一二两三四五六七八九十百千万%％集部岁分秒天周月年次条个])以上|上述")
# "以上3部" / "以上都" after naming fewer titles than that sums up the cards the answer never named as well, and so does
# "以上全部" however many were named.
_COUNT = re.compile(r"(?<![第0-9一二两三四五六七八九十])([0-9]{1,4}|[一二两三四五六七八九十]{1,3})\s*(?:部|个|条|款|套)")
_EVERY = re.compile(r"全部|所有")
_PLURAL = re.compile(r"都|均|这些|这几|它们")
_NUMERALS = {numeral: value for value, numeral in enumerate("零一二三四五六七八九")} | {"两": 2}
# Some accounts, however worded: "其他几个账号", "所有账户", "另外的账号", "其他号".
_SOME_ACCOUNTS = r"(?:的|几个|所有|[0-9一二两三四五六七八九十]+个)?(?:账号|账户|号(?![码称]))"
_REST = re.compile(rf"(?:其余|其他|其它|剩下|剩余|余下)(?!{_SOME_ACCOUNTS}|团队)")
ALL, ABOVE, REST = "all", "above", "rest"
# Latin words: a title this run returned may be written without 《》. Two other words in a row are taken for a drama
# named without 《》 that nothing returned speaks for; one word is a theater, platform or language far more often than a
# drama (DramaBox, YouTube, en). A run of words breaks at anything but spaces and tabs; one pass reads them all.
_LATIN_TOKEN = re.compile(r"[a-z0-9'’&]+")
_LETTER = re.compile(r"[a-z]")
_NOT_NAMES = frozenset({"youtube", "tiktok", "facebook", "instagram", "fb", "ig", "yt", "us", "en", "ko", "ja", "es", "pt", "th", "zh"})
# The trie key a title's last word leads to; no word is empty.
_END = ""
# A bare title as a clause's marked text writes it, like a bracketed one with its inside blanked out.
_BARE_MARK = "《_》"
# A clause of titles only ("《A》", "2. 《B》和《C》", a bare title) says nothing of its own.
_LISTED = re.compile(r"\s*(?:[-*•·+>]+\s*|[0-9]{1,3}\s*[.、)）]\s*)?(?:《_*》(?:\s|[、/&+]|和|与|及|跟|以及|还有)*)+")
# A clause about the whole team, or the accounts besides one, is not about the account a posted_account query cleared.
_TEAM_WORDS = re.compile(rf"团队|全队|哪个账号|(?:所有|任何|任意|任一|每一?个|各个?|全部|其他|其余|其它|别的|另外){_SOME_ACCOUNTS}")
# An account the records never name is one no query cleared: a clause about it is about the team.
_ACCOUNT_WORD = re.compile(r"账号|账户")
# Where _Accounts.named blanked out an account the records name; "A 以外" / "除 A 外" / "非 A" are the accounts besides A.
_BLANK = "\x00"
_BESIDES = re.compile(rf"(?:除了?|除去|除开|非)\s*{_BLANK}|{_BLANK}\s*(?:账号|账户)?\s*(?:以外|之外|外)")
# "除了《A》都没发过" / "除《A》外" / "《A》以外其余都没发过" / "（《A》除外）" sum up every title but the ones the clause names,
# bracketed or bare ("Lost Heir 以外").
_EXCEPT = re.compile(r"除了|除去|除开|除(?=\s*《)|》\s*(?:以外|之外|除外)")
# "除了 A 账号都没发过" leaves an account out, not titles, unless a title follows ("除了 A 账号发过的《X》其余都没发过"): an
# account the records name, one written in Latin letters ("除了 C 账号"), or the account queried.
_ACCOUNT_LEFT_OUT = re.compile(rf"(?:除了|除去|除开)\s*(?:{_BLANK}|[0-9a-z_.@-]+\s*(?:账号|账户)|(?:该|这个|此|本)(?:账号|账户))")
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
        """The accounts the clause names (_TEAM for the whole team, the accounts besides one, or an account no record
        names), and its casefolded text with them blanked out."""
        folded, named = clause.casefold(), set()
        if self.pattern is not None:
            named.update(self.pattern.findall(folded))
            folded = self.pattern.sub(_BLANK, folded)
        if _THIS_ACCOUNT.search(clause):
            named |= self.queried
        if _TEAM_WORDS.search(clause) or _BESIDES.search(folded) or (not named and _ACCOUNT_WORD.search(folded)):
            named.add(_TEAM)
        return frozenset(named), folded


def _latin_runs(folded: str) -> list[list[re.Match]]:
    """The runs of Latin tokens (letters, digits, apostrophes, &) with nothing but spaces and tabs between them."""
    runs: list[list[re.Match]] = []
    end = None
    for match in _LATIN_TOKEN.finditer(folded):
        if end is None or folded[end : match.start()].strip(" \t"):
            runs.append([])
        runs[-1].append(match)
        end = match.end()
    return runs


class _BareTitles:
    """The titles this run returned that are all Latin words, in a word trie, to find them written without 《》.

    Finding the longest title at a position walks the words from there as far as some title goes, not through every
    title that starts with the same word.
    """

    def __init__(self, seen: dict[str, Seen]) -> None:
        self.trie: dict = {}
        for key, entry in seen.items():
            words = _LATIN_TOKEN.findall(key)
            if words and " ".join(words) == key:
                node = self.trie
                for word in words:
                    node = node.setdefault(word, {})
                node.setdefault(_END, entry.title)

    def _title_at(self, run: list[re.Match], index: int) -> tuple[str, int] | None:
        """The longest title the run spells from index ("lost heir 2" over "lost heir"), and how many words it takes."""
        node, found = self.trie, None
        for offset in range(index, len(run)):
            node = node.get(run[offset].group())
            if node is None:
                break
            if _END in node:
                found = node[_END], offset + 1 - index
        return found

    def scan(self, folded: str) -> tuple[list[tuple[str, int, int]], bool]:
        """The returned titles folded text names bare, with where they start and end; and whether two other words in a
        row name something unplaced."""
        found, unknown = [], False
        for run in _latin_runs(folded):
            index, word_before = 0, False
            while index < len(run):
                hit = self._title_at(run, index)
                if hit is not None:
                    found.append((hit[0], run[index].start(), run[index + hit[1] - 1].end()))
                    index, word_before = index + hit[1], False
                    continue
                word = run[index].group() not in _NOT_NAMES and _LETTER.search(run[index].group()) is not None
                unknown = unknown or (word and word_before)
                index, word_before = index + 1, word
        return found, unknown


def _marked(folded: str, found: list[tuple[str, int, int]]) -> str:
    """folded with each bare title found written as _BARE_MARK."""
    parts, end = [], 0
    for _, start, stop in found:
        parts.extend((folded[end:start], _BARE_MARK))
        end = stop
    parts.append(folded[end:])
    return "".join(parts)


def _excepts(marked: str) -> bool:
    """Whether a clause's marked text leaves titles out; leaving out an account with no title after it does not."""
    last_title = marked.rfind("《")
    return any(not (_ACCOUNT_LEFT_OUT.match(marked, match.start()) and last_title < match.start()) for match in _EXCEPT.finditer(marked))


class _Subject(NamedTuple):
    """What a claim is about: the titles named, whether an unplaced name is, what it sums up (None, ALL, ABOVE, REST);
    and from where."""

    titles: tuple[str, ...]
    unknown: bool
    summary: str | None
    start: int
    # The indent of the line it starts on: a deeper line below is a detail of it.
    indent: int
    # The fewest titles an ABOVE claim sums up: fewer named up to it, and it sums up every record returned.
    least: int = 1


def _summary(masked: str, left: int, right: int) -> str | None:
    if _REST.search(masked, left, right):
        return REST
    if _ABOVE.search(masked, left, right):
        return ABOVE
    return ALL if _SUMMARY.search(masked, left, right) else None


def _number(word: str) -> int | None:
    """An ASCII or Chinese count up to 99 ("3", "三", "十二", "二十"); None for anything else ("二三")."""
    if word.isascii():
        return int(word)
    tens, ten, ones = word.partition("十")
    if not ten:
        return _NUMERALS.get(word) if len(word) == 1 else None
    tens_value = _NUMERALS.get(tens) if tens else 1
    ones_value = _NUMERALS.get(ones) if ones else 0
    return None if tens_value is None or ones_value is None else tens_value * 10 + ones_value


def _least(masked: str, left: int, right: int) -> int:
    """The fewest titles an "以上" clause sums up: its largest count ("以上3部"), every card for "以上全部", two for a
    plural ("以上都"), else one."""
    numbers = [number for count in _COUNT.finditer(masked, left, right) if (number := _number(count.group(1))) is not None]
    if numbers:
        return max(*numbers, 1)
    if _EVERY.search(masked, left, right):
        return sys.maxsize
    return 2 if _PLURAL.search(masked, left, right) else 1


class _Own(NamedTuple):
    """A clause read on its own: its subject, the accounts and titles it names, whether it only lists titles, and
    whether it leaves titles out without summing up ("除了《A》，都没发过" sums up in the next clause)."""

    subject: _Subject
    named: frozenset[str]
    titles: tuple[str, ...]
    listed: bool
    excepting: bool


def _own_subject(text: str, masked: str, clause: tuple[_Accounts, _BareTitles], left: int, right: int, indent: int) -> _Own:
    accounts, bare = clause
    named, words = accounts.named(masked[left:right])
    found, unknown = bare.scan(words)
    marked = _marked(words, found)
    bracketed = (match.group(1).strip() for match in _TITLE.finditer(text, left, right))
    titles = tuple(dict.fromkeys(title for title in (*bracketed, *(title for title, _, _ in found)) if title))
    summary, excepts, listed = _summary(masked, left, right), _excepts(marked), _LISTED.fullmatch(marked) is not None
    if summary is not None and excepts:
        return _Own(_Subject((), unknown, REST, left, indent), named, titles, listed, False)
    least = _least(masked, left, right) if summary == ABOVE else 1
    return _Own(_Subject(titles, unknown, summary, left, indent, least), named, titles, listed, excepts and summary is None)


class _Clauses(NamedTuple):
    starts: list[int]
    subjects: list[_Subject]
    # The accounts a claim in the clause is about.
    mentions: list[frozenset[str]]
    # The titles each clause names itself, bracketed or bare.
    owns: list[tuple[str, ...]]


def _widened(subject: _Subject, listed: list[str], excepting: bool) -> _Subject:
    """A summing-up clause after the clauses it goes on from: after "除了《A》，" it sums up the rest; naming titles itself,
    it also sums up the titles listed right before it ("《A》，《B》都没发过")."""
    if subject.summary != ALL:
        return subject
    if excepting and not subject.titles:
        return subject._replace(summary=REST)
    if subject.titles and listed:
        return subject._replace(titles=tuple(dict.fromkeys((*listed, *subject.titles))))
    return subject


def _clauses(text: str, masked: str, seen: dict[str, Seen]) -> _Clauses:
    """Each clause's start, its subject, the accounts it is about, and the titles it names itself.

    A clause naming nothing that continues the one before takes that one's subject. A comma continues the clause before,
    and so does a line indented deeper than the line its subject starts on (a list item's details under its title). A
    sentence end, a semicolon, a blank line or a line at the same depth never does. A clause naming an account is about
    that account; one naming none that continues the one before is about the accounts that one is about. Clauses of
    titles only, one after another, list titles for the next clause to sum up; a sentence end or a blank line ends the
    list.
    """
    found = _Clauses([], [], [], [])
    reading = (_Accounts(seen), _BareTitles(seen))
    left, soft, indent = 0, False, _INDENT.match(masked).end()
    listed: list[str] = []
    excepting = False
    for mark in (*_CLAUSE_MARK.finditer(masked), None):
        own = _own_subject(text, masked, reading, left, mark.start() if mark else len(masked), indent)
        subject = _widened(own.subject, listed, excepting)
        borrows = soft and not (subject.titles or subject.unknown or subject.summary)
        found.subjects.append(found.subjects[-1] if borrows else subject)
        found.mentions.append((own.named or found.mentions[-1]) if borrows else own.named)
        found.owns.append(own.titles)
        found.starts.append(left)
        if mark is None:
            break
        left = mark.end()
        # Extended in place: a list of thousands of titles is read once, not copied at each one.
        if own.listed and mark.group() in _LIST_MARKS:
            listed.extend(own.titles)
        else:
            listed = []
        excepting = mark.group() in "，," and (own.excepting or (excepting and subject.summary is None))
        if mark.group() == "\n":
            indent = _INDENT.match(masked, left).end() - left
            soft = masked[left + indent : left + indent + 1] not in ("", "\n") and indent > found.subjects[-1].indent
        else:
            soft = mark.group() in "，,"
    return found


def _listed(titles: dict[str, str]) -> str:
    return "、".join(f"《{title}》" for title in list(titles.values())[:5])


def _clearing(entry: Seen) -> frozenset[str]:
    """The accounts a posted_account query returned the title for and the records never name as posting it."""
    return entry.clear - entry.posters if entry.status == POSTED else frozenset()


def _cleared(entry: Seen, mentioned: frozenset[str]) -> bool:
    """A claim naming only accounts a posted_account query cleared, and the records never name, is about those accounts.

    _TEAM is in no clear set: a claim that also names the team stays refuted by the team's posts.
    """
    return bool(mentioned) and mentioned <= _clearing(entry)


def _effective(entry: Seen, mentioned: frozenset[str]) -> str:
    return UNPOSTED if _cleared(entry, mentioned) else entry.status


class _Findings:
    """Across an answer's claims: the titles whose records refute one, and whether one stood on nothing."""

    def __init__(self, posted_checked: bool) -> None:
        self.posted_checked = posted_checked
        self.unmatched: dict[str, str] = {}
        self.posted: dict[str, str] = {}
        self.unfiltered = False

    def add(self, norm: str, title: str, status: str) -> None:
        """One record a claim stands on: unmatched and posted refute it; an unknown one needs the posted filter."""
        if status == UNMATCHED:
            self.unmatched.setdefault(norm, title)
        elif status == POSTED:
            self.posted.setdefault(norm, title)
        elif status == UNKNOWN:
            self.unplaced()

    def unplaced(self) -> None:
        """A claim about an unplaced name, or about no record at all: only the posted filter backs it."""
        self.unfiltered = self.unfiltered or not self.posted_checked

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
    """Judges an answer's claims in order: the first "其余" reads the most, and "以上" reads only the titles named since
    the last one, unless it sums up more titles than were named (then every record returned).

    Judging only adds, so each record is read about once however many claims stand on it. Unmatched, unknown and posted
    records no posted_account query cleared say the same to every claim: each is read once for every record, once for
    those not named yet, and once as "以上" reaches it. A posted record some query cleared says something else to a claim
    about the accounts cleared: once a claim is refuted by it, it is read no more, so it is read again only by claims
    naming only accounts that queries cleared.
    """

    def __init__(self, seen: dict[str, Seen], posted_checked: bool, owns: list[tuple[str, ...]]) -> None:
        self.seen, self.owns = seen, owns
        self.findings = _Findings(posted_checked)
        # The titles named in the clauses before self.through, in order; and how many returned records are not.
        self.named: set[str] = set()
        self.order: list[str] = []
        self.through = 0
        self.unnamed = len(seen)
        self.done: set[tuple] = set()
        self.above: dict[tuple, int] = {}
        # Where each record came back: a claim about every record reads them, and lists those refuting it, in this order.
        self.rank = {norm: position for position, norm in enumerate(seen)}
        fixed = {norm: entry for norm, entry in seen.items() if entry.status != UNPOSTED and not _clearing(entry)}
        self.has_fixed = bool(fixed)
        # Fixed records not read yet for every record, and for those not named; "以上" has read order[:fixed_through].
        self.fixed_all, self.fixed_rest, self.fixed_through = fixed, dict(fixed), 0
        # Cleared posted records no claim has refuted yet: all of them, those not named, and those named (where, as named).
        self.every_clearable = [entry for entry in seen.values() if _clearing(entry)]
        self.clearable = {norm: entry for norm, entry in seen.items() if _clearing(entry)}
        self.rest_clearable = dict(self.clearable)
        self.named_clearable: dict[str, tuple[int, str]] = {}

    def _name(self, title: str) -> None:
        norm = _norm(title)
        if norm in self.named:
            return
        self.named.add(norm)
        self.order.append(title)
        if norm in self.seen:
            self.unnamed -= 1
            self.fixed_rest.pop(norm, None)
            self.rest_clearable.pop(norm, None)
            if norm in self.clearable:
                self.named_clearable[norm] = (len(self.order) - 1, title)

    def _name_through(self, index: int) -> None:
        for titles in self.owns[self.through : index + 1]:
            for title in titles:
                self._name(title)
        self.through = max(self.through, index + 1)

    def _pooled(self, fixed: dict[str, Seen], clearable: dict[str, Seen]) -> list[tuple[int, str, str]]:
        """A fixed pool, read now and never again, and a clearable one, as the records returned order them."""
        found = [(self.rank[norm], norm, entry.title) for pool in (fixed, clearable) for norm, entry in pool.items()]
        fixed.clear()
        return found

    def _read(self, found: list[tuple[int, str, str]], mentioned: frozenset[str]) -> None:
        """Records a claim stands on, as (position, normalized title, title), read in order: a cleared posted record
        refutes a claim about accounts not all cleared, and is read no more once it does."""
        for _, norm, title in sorted(found):
            entry = self.seen.get(norm)
            if entry is None or not _clearing(entry):
                self.findings.add(norm, title, UNKNOWN if entry is None else entry.status)
            elif norm in self.clearable and not _cleared(entry, mentioned):
                self.findings.add(norm, title, POSTED)
                self.clearable.pop(norm, None)
                self.rest_clearable.pop(norm, None)
                self.named_clearable.pop(norm, None)

    def claim(self, subject: _Subject, mentioned: frozenset[str], index: int) -> None:
        self._name_through(index)
        if subject.summary == REST and not subject.titles:
            self._rest(subject, mentioned)
        elif subject.summary == ABOVE and self.order:
            self._above(subject, mentioned)
        elif subject.titles:
            self._titles(subject, mentioned)
        else:
            self._everything(subject, mentioned)

    def _titles(self, subject: _Subject, mentioned: frozenset[str]) -> None:
        key = (subject.start, mentioned)
        if key in self.done:
            return
        self.done.add(key)
        missing = Seen("", UNKNOWN)
        for title in subject.titles:
            self.findings.add(_norm(title), title, _effective(self.seen.get(_norm(title), missing), mentioned))
        if subject.unknown:
            self.findings.unplaced()

    def _rest(self, subject: _Subject, mentioned: frozenset[str]) -> None:
        """Every record returned but the titles named: with none, only the posted filter backs it."""
        key = (REST, mentioned, subject.unknown)
        if key in self.done:
            return
        self.done.add(key)
        if not self.unnamed:
            self.findings.unplaced()
            return
        self._read(self._pooled(self.fixed_rest, self.rest_clearable), mentioned)
        if subject.unknown:
            self.findings.unplaced()

    def _above(self, subject: _Subject, mentioned: frozenset[str]) -> None:
        """The titles named since the last "以上" about the same accounts, and every record when it sums up more."""
        key = (ABOVE, mentioned, subject.unknown)
        begin = self.above.get(key, 0)
        if begin < len(self.order):
            # The fixed records named since any "以上" read them, and the cleared ones named since this one's last.
            found = [
                (position, _norm(title), title)
                for position, title in enumerate(self.order[self.fixed_through :], self.fixed_through)
                if _norm(title) not in self.named_clearable
            ]
            for norm, (position, title) in reversed(self.named_clearable.items()):
                if position < begin:
                    break
                found.append((position, norm, title))
            self.fixed_through = len(self.order)
            self._read(found, mentioned)
            if subject.unknown:
                self.findings.unplaced()
            self.above[key] = len(self.order)
        if len(self.order) < subject.least:
            self._everything(subject, mentioned)

    def _everything(self, subject: _Subject, mentioned: frozenset[str]) -> None:
        key = (None, mentioned, subject.unknown, subject.summary is not None)
        if key in self.done:
            return
        self.done.add(key)
        if subject.summary is not None or self.findings.posted_checked:
            if not self.seen:
                self.findings.unplaced()
                return
            self._read(self._pooled(self.fixed_all, self.clearable), mentioned)
            if subject.unknown:
                self.findings.unplaced()
        elif subject.unknown or not self.seen or self.has_fixed or any(not _cleared(entry, mentioned) for entry in self.every_clearable):
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
    # A dict keeps the titles in order and finds a repeated one at once, however many there are.
    unknown = dict.fromkeys(title for title in (match.group(1).strip() for match in _TITLE.finditer(text)) if title and _norm(title) not in known)
    if unknown:
        notes.append("正文提到的" + "、".join(f"《{t}》" for t in list(unknown)[:5]) + "不在本轮查询结果中，请以候选卡为准。")
    if _claims(_SAVE_CLAIM, text):
        notes.append("本轮没有写入个人清单；只有点击「确认保存」并看到回执才算保存。")
    notes.extend(_not_posted_notes(text, posted_checked, posted_seen or {}))
    return notes
