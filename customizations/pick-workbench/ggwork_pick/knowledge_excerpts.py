"""Which parts of a knowledge document pick_search_knowledge returns.

The RealShort rules arrive as one Markdown document whose signal-kind list near the top names most theaters, with each
theater's own section further down. A window anchored on the earliest hit returned that list and cut the sections off,
so a theater's YouTube, filing and tag rules came back unknown or from its neighbour.
"""

import re

EXCERPT_CHARS = 1600
# The whole RealShort rules document (about 2,800 characters for ten theaters) fits; longer documents are excerpted.
WHOLE_DOCUMENT_CHARS = 4000
_HEADING = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t]*$", re.MULTILINE)


def _sections(text: str) -> list[tuple[str, int, int]]:
    """(casefolded heading, start, end) per Markdown heading.

    A section runs to the next heading of its level or above, less the blank lines before it.
    """
    heads = [(len(match.group(1)), match.group(2).casefold(), match.start()) for match in _HEADING.finditer(text)]
    ends = [next((later for depth, _, later in heads[index + 1 :] if depth <= level), len(text)) for index, (level, _, _) in enumerate(heads)]
    return [(heading, start, start + len(text[start:end].rstrip())) for (_, heading, start), end in zip(heads, ends)]


def excerpt_spans(text: str, words: list[str]) -> list[tuple[int, int]]:
    """The (start, end) spans of text to return for casefolded query words, in document order.

    The whole document when it is short. Otherwise the sections whose heading names a query word, keeping only the
    innermost of nested ones so a theater's section beats the title heading around it, each cut to EXCERPT_CHARS;
    failing that, a window from just before the earliest hit.
    """
    if len(text) <= WHOLE_DOCUMENT_CHARS:
        return [(0, len(text))]
    named = [(start, end) for heading, start, end in _sections(text) if any(word in heading for word in words)]
    innermost = [span for span in named if not any(other != span and span[0] <= other[0] and other[1] <= span[1] for other in named)]
    if innermost:
        return [(start, min(end, start + EXCERPT_CHARS)) for start, end in innermost]
    folded = text.casefold()
    start = max(0, min((folded.find(word) for word in words if word in folded), default=0) - 100)
    return [(start, start + EXCERPT_CHARS)]
