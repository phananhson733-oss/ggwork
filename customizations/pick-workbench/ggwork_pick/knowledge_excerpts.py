"""Which parts of a knowledge document pick_search_knowledge returns.

The RealShort rules arrive as one Markdown document whose signal-kind list near the top names most theaters, with each
theater's own section further down. A window anchored on the earliest hit returned that list and cut the sections off,
so a theater's YouTube, filing and tag rules came back unknown or from its neighbour.

With several documents, each gets an excerpt before any gets a second, and all of them stay within a budget: the host
externalizes a longer tool result, and the pick agent cannot open what it put aside. Each entry pays for its own
fields, so one document with a long source ref does not crowd out the others; an excerpt cut short says so, and what
the limits leave out is counted.

Uploads can reach 25MB and this runs on the event loop, so every pass is linear in the text or the heading count.
"""

import json
import re
from typing import NamedTuple

EXCERPT_CHARS = 1600
# The whole RealShort rules document (about 2,800 characters for ten theaters) fits; longer documents are excerpted.
WHOLE_DOCUMENT_CHARS = 4000
# The tool returns at most five excerpts in all; more would only be dropped.
MAX_EXCERPTS = 5
# What excerpt_spans cuts each excerpt to by default, in characters once JSON-escaped; the tool's budget comes from its
# output limit, through choose_excerpts.
EXCERPT_BUDGET = 8000
# The least the excerpts get: documents whose entries would leave them less are left out.
MIN_EXCERPT_BUDGET = 1000
_HEADING = re.compile(r"^(#{1,6})[ \t]+(.*)$", re.MULTILINE)
_VISIBLE = re.compile(r"\S")
# How much of a text _unfolded casefolds at a time on its way to a position.
_FOLD_CHUNK = 1 << 16


class Excerpt(NamedTuple):
    """An excerpt of texts[index], and whether the part of the document it is from goes on past it."""

    index: int
    start: int
    end: int
    truncated: bool


def _escaped(text: str) -> int:
    """The characters text takes inside the tool's JSON output: a newline or a quote counts two."""
    return len(json.dumps(text, ensure_ascii=False)) - 2


def fit(text: str, budget: int) -> str:
    """text, or its longest start that with "…" takes at most budget characters once escaped."""
    if _escaped(text) <= budget:
        return text
    low, high = 0, len(text)
    while low < high:
        middle = (low + high + 1) // 2
        low, high = (middle, high) if _escaped(text[:middle]) + 1 <= budget else (low, middle - 1)
    return text[:low] + "…"


def _cut(text: str, start: int, end: int, budget: int) -> tuple[int, int, bool]:
    """start to at most end, at most EXCERPT_CHARS, and at most budget characters once escaped; and whether anything but
    whitespace before end is left out."""
    stop = min(end, start + EXCERPT_CHARS)
    if _escaped(text[start:stop]) > budget:
        low, high = start, stop
        while low < high:
            middle = (low + high + 1) // 2
            low, high = (middle, high) if _escaped(text[start:middle]) <= budget else (low, middle - 1)
        stop = low
    return start, stop, _VISIBLE.search(text, stop, end) is not None


def _trimmed(text: str, start: int, end: int, budget: int) -> tuple[int, int, bool]:
    """_cut, less the blank lines before the next heading."""
    start, stop, truncated = _cut(text, start, end, budget)
    return start, start + len(text[start:stop].rstrip()), truncated


def _named_sections(text: str, words: list[str]) -> list[tuple[int, int]]:
    """(start, end) of the sections to return for casefolded query words, in document order.

    A section runs to the next heading of its level or above, so sections nest like a tree. Of the sections whose own
    heading names a query word, those whose heading and the headings above it name the most query words: under a
    "## DramaBox", its "### YouTube" names both words of "DramaBox YouTube" and the other theaters' ones name one.
    """
    heads = [(len(match.group(1)), match.group(2).strip().casefold(), match.start()) for match in _HEADING.finditer(text)]
    ends = [len(text)] * len(heads)
    covered: list[frozenset[str]] = []
    named: list[int] = []
    open_heads: list[int] = []
    for index, (level, heading, start) in enumerate(heads):
        while open_heads and heads[open_heads[-1]][0] >= level:
            ends[open_heads.pop()] = start
        own = frozenset(word for word in words if word in heading)
        covered.append(own | covered[open_heads[-1]] if open_heads else own)
        if own:
            named.append(index)
        open_heads.append(index)
    best = max((len(covered[index]) for index in named), default=0)
    return _innermost([(heads[index][2], ends[index]) for index in named if len(covered[index]) == best])


def _innermost(spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Spans containing none of the others. Sections nest or are disjoint, so only the next one can be inside."""
    return [span for span, following in zip(spans, [*spans[1:], None]) if following is None or following[0] >= span[1]]


def _unfolded(text: str, offset: int) -> int:
    """The position in text of the character that text.casefold()[offset] comes from. Casefolding goes character by
    character, so it is summed a chunk at a time and then searched within the one chunk."""
    start = folded = 0
    while start < len(text):
        length = len(text[start : start + _FOLD_CHUNK].casefold())
        if folded + length > offset:
            break
        start, folded = start + _FOLD_CHUNK, folded + length
    low, high = start, min(len(text), start + _FOLD_CHUNK)
    while low < high:
        middle = (low + high + 1) // 2
        low, high = (middle, high) if folded + len(text[start:middle].casefold()) <= offset else (low, middle - 1)
    return low


def _first_hit(text: str, words: list[str]) -> int:
    """Where the earliest query word starts in text.

    One case-insensitive search of text itself finds it for almost every word: positions in text.casefold() lie further
    on after each character that folds to two ("ß", "İ"). A word only casefolding matches ("strasse" for "Straße") is
    found there instead, and its position mapped back.
    """
    if not words:
        return 0
    hit = re.search("|".join(map(re.escape, words)), text, re.IGNORECASE)
    if hit is not None:
        return hit.start()
    folded = text.casefold()
    offsets = [offset for offset in map(folded.find, words) if offset != -1]
    return _unfolded(text, min(offsets)) if offsets else 0


def _excerpts(text: str, words: list[str], budget: int) -> tuple[list[tuple[int, int, bool]], int]:
    """The spans excerpt_spans returns, each with whether it is cut short; and how many there are to return in all (the
    named sections, however many; else one)."""
    if len(text) <= WHOLE_DOCUMENT_CHARS and _escaped(text) <= budget:
        return [(0, len(text), False)], 1
    named = _named_sections(text, words)
    if named:
        return [_trimmed(text, start, end, budget) for start, end in named[:MAX_EXCERPTS]], len(named)
    return [_cut(text, max(0, _first_hit(text, words) - 100), len(text), budget)], 1


def excerpt_spans(text: str, words: list[str], budget: int = EXCERPT_BUDGET) -> list[tuple[int, int]]:
    """The (start, end) spans of text to return for casefolded query words, in document order, each within budget.

    The whole document when it is short. Otherwise the sections whose heading names the most query words, keeping only
    the innermost of nested ones so a theater's section beats the title heading around it, each cut to EXCERPT_CHARS;
    failing that, a window from just before the earliest hit.
    """
    return [(start, end) for start, end, _ in _excerpts(text, words, budget)[0]]


def choose_excerpts(texts: list[str], words: list[str], budget: int, costs: list[int], limit: int = MAX_EXCERPTS) -> tuple[list[Excerpt], int]:
    """The excerpts to return from texts ranked best first, at most limit in all, and how many the limits leave out.

    budget is what the entries take in all, and costs[i] what an entry for texts[i] takes besides its excerpt. The texts
    best first whose entries leave the excerpts MIN_EXCERPT_BUDGET are kept, and the rest left out. Each kept text in
    turn gives its next excerpt, so every one gets one before any gets a second; each is cut to an equal share of what
    the entries leave, and a later one is added, with its own entry, only while the total stays within budget. A text
    left out counts as one excerpt left out.
    """
    kept, reserved = [], 0
    for index, cost in enumerate(costs[: min(len(texts), limit)]):
        if reserved + cost <= budget - MIN_EXCERPT_BUDGET:
            kept.append(index)
            reserved += cost
    if not kept:
        return [], len(texts)
    share = (budget - reserved) // len(kept)
    queues = {index: _excerpts(texts[index], words, share) for index in kept}
    chosen, spent = [], reserved
    for depth in range(limit):
        for index in kept:
            spans = queues[index][0]
            if depth < len(spans) and len(chosen) < limit:
                start, end, truncated = spans[depth]
                cost = _escaped(texts[index][start:end]) + (costs[index] if depth else 0)
                if spent + cost <= budget:
                    chosen.append(Excerpt(index, start, end, truncated))
                    spent += cost
    offered = sum(total for _, total in queues.values()) + len(texts) - len(kept)
    return sorted(chosen), offered - len(chosen)
