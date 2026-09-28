"""Which parts of a knowledge document pick_search_knowledge returns.

The RealShort rules arrive as one Markdown document whose signal-kind list near the top names most theaters, with each
theater's own section further down. A window anchored on the earliest hit returned that list and cut the sections off,
so a theater's YouTube, filing and tag rules came back unknown or from its neighbour.

Uploads can reach 25MB and this runs on the event loop, so every pass is linear in the text or the heading count.
"""

import re

EXCERPT_CHARS = 1600
# The whole RealShort rules document (about 2,800 characters for ten theaters) fits; longer documents are excerpted.
WHOLE_DOCUMENT_CHARS = 4000
# The tool returns at most five excerpts in all; more from one document would only be dropped.
MAX_EXCERPTS = 5
_HEADING = re.compile(r"^(#{1,6})[ \t]+(.*)$", re.MULTILINE)


def _sections(text: str) -> list[tuple[str, int, int]]:
    """(casefolded heading, start, end) per Markdown heading, in document order.

    A section runs to the next heading of its level or above, so sections nest like a tree.
    """
    heads = [(len(match.group(1)), match.group(2).strip().casefold(), match.start()) for match in _HEADING.finditer(text)]
    ends = [len(text)] * len(heads)
    open_heads: list[int] = []
    for index, (level, _, start) in enumerate(heads):
        while open_heads and heads[open_heads[-1]][0] >= level:
            ends[open_heads.pop()] = start
        open_heads.append(index)
    return [(heading, start, end) for (_, heading, start), end in zip(heads, ends)]


def _innermost(spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Spans containing none of the others. Sections nest or are disjoint, so only the next one can be inside."""
    return [span for span, following in zip(spans, [*spans[1:], None]) if following is None or following[0] >= span[1]]


def _trimmed(text: str, start: int, end: int) -> tuple[int, int]:
    """At most EXCERPT_CHARS from start, less the blank lines before the next heading."""
    end = min(end, start + EXCERPT_CHARS)
    return start, start + len(text[start:end].rstrip())


def excerpt_spans(text: str, words: list[str]) -> list[tuple[int, int]]:
    """The (start, end) spans of text to return for casefolded query words, in document order.

    The whole document when it is short. Otherwise the sections whose heading names a query word, keeping only the
    innermost of nested ones so a theater's section beats the title heading around it, each cut to EXCERPT_CHARS;
    failing that, a window from just before the earliest hit.
    """
    if len(text) <= WHOLE_DOCUMENT_CHARS:
        return [(0, len(text))]
    named = [(start, end) for heading, start, end in _sections(text) if any(word in heading for word in words)]
    if named:
        return [_trimmed(text, start, end) for start, end in _innermost(named)[:MAX_EXCERPTS]]
    folded = text.casefold()
    start = max(0, min((folded.find(word) for word in words if word in folded), default=0) - 100)
    return [(start, start + EXCERPT_CHARS)]
