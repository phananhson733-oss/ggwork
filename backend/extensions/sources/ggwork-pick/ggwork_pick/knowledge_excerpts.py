"""Which parts of a knowledge document pick_search_knowledge returns.

The RealShort rules arrive as one Markdown document whose signal-kind list near the top names most theaters, with each
theater's own section further down. A window anchored on the earliest hit returned that list and cut the sections off,
so a theater's YouTube, filing and tag rules came back unknown or from its neighbour.

With several documents, each gets an excerpt before any gets a second, and all of them stay within a budget: the host
externalizes a longer tool result, and the pick agent cannot open what it put aside.

Uploads can reach 25MB and this runs on the event loop, so every pass is linear in the text or the heading count.
"""

import json
import re

EXCERPT_CHARS = 1600
# The whole RealShort rules document (about 2,800 characters for ten theaters) fits; longer documents are excerpted.
WHOLE_DOCUMENT_CHARS = 4000
# The tool returns at most five excerpts in all; more would only be dropped.
MAX_EXCERPTS = 5
# All excerpts together, in characters once JSON-escaped; the tool passes what its output limit leaves.
EXCERPT_BUDGET = 8000
_HEADING = re.compile(r"^(#{1,6})[ \t]+(.*)$", re.MULTILINE)


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


def _cut(text: str, start: int, end: int, budget: int) -> tuple[int, int]:
    """start to at most end, at most EXCERPT_CHARS, and at most budget characters once escaped."""
    end = min(end, start + EXCERPT_CHARS)
    if _escaped(text[start:end]) <= budget:
        return start, end
    low, high = start, end
    while low < high:
        middle = (low + high + 1) // 2
        low, high = (middle, high) if _escaped(text[start:middle]) <= budget else (low, middle - 1)
    return start, low


def _trimmed(text: str, start: int, end: int, budget: int) -> tuple[int, int]:
    """_cut, less the blank lines before the next heading."""
    start, end = _cut(text, start, end, budget)
    return start, start + len(text[start:end].rstrip())


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


def excerpt_spans(text: str, words: list[str], budget: int = EXCERPT_BUDGET) -> list[tuple[int, int]]:
    """The (start, end) spans of text to return for casefolded query words, in document order, each within budget.

    The whole document when it is short. Otherwise the sections whose heading names the most query words, keeping only
    the innermost of nested ones so a theater's section beats the title heading around it, each cut to EXCERPT_CHARS;
    failing that, a window from just before the earliest hit.
    """
    if len(text) <= WHOLE_DOCUMENT_CHARS and _escaped(text) <= budget:
        return [(0, len(text))]
    named = _named_sections(text, words)
    if named:
        return [_trimmed(text, start, end, budget) for start, end in named[:MAX_EXCERPTS]]
    folded = text.casefold()
    start = max(0, min((folded.find(word) for word in words if word in folded), default=0) - 100)
    return [_cut(text, start, len(text), budget)]


def choose_excerpts(texts: list[str], words: list[str], budget: int = EXCERPT_BUDGET, limit: int = MAX_EXCERPTS) -> list[tuple[int, int, int]]:
    """(text index, start, end) of the excerpts to return from texts ranked best first, at most limit in all.

    Each text in turn gives its next excerpt, so every matching document gets one before any gets a second; each is cut
    to an equal share of the budget, and a later one is added only while the total stays within it.
    """
    texts = texts[:limit]
    if not texts:
        return []
    queues = [excerpt_spans(text, words, budget // len(texts)) for text in texts]
    chosen, spent = [], 0
    for depth in range(limit):
        for index, spans in enumerate(queues):
            if depth < len(spans) and len(chosen) < limit:
                start, end = spans[depth]
                cost = _escaped(texts[index][start:end])
                if spent + cost <= budget:
                    chosen.append((index, start, end))
                    spent += cost
    return sorted(chosen)
