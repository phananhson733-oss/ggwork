"""The page set P of one identity (plan TR-09, D25; section 2 premise 3; design 5.5).

P comes from the identity and the round's frozen inputs only (FrozenInputs, D36): the canonical book id, the frozen
mirror version's rs_ids (the non-canonical ids that point at it) and the frozen legacy snapshot. It never reads a row of
C, D or E. Collecting P from the detail would leave a drama that is missing from the detail with an empty P, both lower
bounds would then be empty and "agree", and a lost page would read as a drama with no impressions (counterexample 22).

- New pages: https://<host>/<locale>/drama/<slug>-<id>, anchored, for the canonical id and each non-canonical id.
- Legacy pages: the snapshot's raw URLs whose target is a drama resolving to this identity (book id through rs_ids, same
  locale), matched exactly and verbatim, query string included; blog targets and unresolved ones never count.

X_det filters the detail with PageSet.contains, and the filter requests' includingRegex comes from
PageSet.regex_chunks: both are built from the same alternatives, so they cannot drift. Each URL matches at most one
alternative and so one chunk, and the filter responses of all chunks add up without counting a page twice. The regexes
use only syntax RE2 (GSC's regex dialect) and Python's re read the same way.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal

SITE_HOST = "dramashortstv.com"  # sc-domain:dramashortstv.com; TR-07 confirms how GSC spells the host in page URLs
BOOK_ID = re.compile(r"[0-9a-f]{24}")
HOST = re.compile(r"[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+")
LOCALE = re.compile(r"[a-z]{2}(-[A-Za-z]{2,4})?")  # as PasteRow's URL pattern (contract_rows)
RE2_SPECIAL = frozenset("\\.+*?()|[]{}^$")
CHUNK_HEAD, CHUNK_TAIL = "^(?:", ")$"

TargetKind = Literal["drama", "blog", "unresolved"]


def re2_escape(text: str) -> str:
    """Escape the regex metacharacters only; everything else stays literal in both RE2 and Python's re."""
    return "".join(f"\\{char}" if char in RE2_SPECIAL else char for char in text)


@dataclass(frozen=True, slots=True)
class LegacyTarget:
    """One row of the frozen legacy snapshot (design 5.5): a raw GSC URL and what RealShort resolved it to."""

    raw_url: str
    target_kind: TargetKind
    locale: str | None
    book_id: str | None


@dataclass(frozen=True, slots=True)
class PageSources:
    """The frozen inputs P is built from. rs_ids maps every id of the frozen mirror version to its canonical id."""

    host: str
    rs_ids: Mapping[str, str]
    legacy: tuple[LegacyTarget, ...]


@dataclass(frozen=True, slots=True)
class RegexPlan:
    """includingRegex chunks, each at most the length limit; overflow lists alternatives too long to fit on their own
    (the identity's filter request is then incomplete: regex_overflow, descriptive only)."""

    chunks: tuple[str, ...]
    overflow: tuple[str, ...]

    @property
    def complete(self) -> bool:
        return not self.overflow


@dataclass(frozen=True, slots=True)
class PageSet:
    host: str
    locale: str
    book_ids: tuple[str, ...]  # the canonical id first, then the non-canonical ones in order
    legacy_urls: tuple[str, ...]  # verbatim, sorted
    skipped_ids: tuple[str, ...]  # rs_ids entries for this identity that no page URL can end with
    _matcher: re.Pattern = field(repr=False, compare=False)

    def contains(self, url: str) -> bool:
        return self._matcher.fullmatch(url) is not None

    @property
    def alternatives(self) -> tuple[str, ...]:
        return _alternatives(self.host, self.locale, self.book_ids, self.legacy_urls)

    def regex_chunks(self, max_length: int) -> RegexPlan:
        """Pack the alternatives greedily into anchored chunks of at most max_length characters (TR-07 measures GSC's
        limit). The new-page ids share one group per chunk, as ^https://<host>/<locale>/drama/[^/?#]+-(?:<id>|...)$."""
        if max_length <= len(CHUNK_HEAD) + len(CHUNK_TAIL):
            raise ValueError(f"max_length {max_length} leaves no room for any alternative")
        units = (*(("id", book_id) for book_id in self.book_ids), *(("url", url) for url in self.legacy_urls))
        chunks: tuple[tuple[tuple[str, str], ...], ...] = ()
        overflow: tuple[str, ...] = ()
        current: tuple[tuple[str, str], ...] = ()
        for unit in units:
            if len(self._render((unit,))) > max_length:
                overflow = (*overflow, *_unit_alternatives(self.host, self.locale, (unit,)))
            elif len(self._render((*current, unit))) <= max_length:
                current = (*current, unit)
            else:
                chunks, current = (*chunks, current), (unit,)
        chunks = (*chunks, current) if current else chunks
        return RegexPlan(tuple(self._render(chunk) for chunk in chunks), overflow)

    def _render(self, units: tuple[tuple[str, str], ...]) -> str:
        return CHUNK_HEAD + "|".join(_unit_alternatives(self.host, self.locale, units)) + CHUNK_TAIL


def _new_page_alternative(host: str, locale: str, book_ids: tuple[str, ...]) -> str:
    return f"https://{re2_escape(host)}/{re2_escape(locale)}/drama/[^/?#]+-(?:{'|'.join(book_ids)})"


def _alternatives(host: str, locale: str, book_ids: tuple[str, ...], legacy_urls: tuple[str, ...]) -> tuple[str, ...]:
    return (_new_page_alternative(host, locale, book_ids), *(re2_escape(url) for url in legacy_urls))


def _unit_alternatives(host: str, locale: str, units: tuple[tuple[str, str], ...]) -> tuple[str, ...]:
    ids = tuple(value for kind, value in units if kind == "id")
    urls = tuple(re2_escape(value) for kind, value in units if kind == "url")
    return (*((_new_page_alternative(host, locale, ids),) if ids else ()), *urls)


def _check(pattern: re.Pattern, value: str, what: str) -> None:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise ValueError(f"{what} {value!r} is not valid")


def page_set(*, canonical_id: str, locale: str, sources: PageSources) -> PageSet:
    """P for the identity (canonical_id, locale), from the frozen inputs alone."""
    _check(BOOK_ID, canonical_id, "canonical book id")
    _check(LOCALE, locale, "locale")
    _check(HOST, sources.host, "host")
    aliases = sorted(book_id for book_id, target in sources.rs_ids.items() if target == canonical_id and book_id != canonical_id)
    book_ids = (canonical_id, *(book_id for book_id in aliases if BOOK_ID.fullmatch(book_id)))
    skipped = tuple(book_id for book_id in aliases if not BOOK_ID.fullmatch(book_id))
    new_page = re.compile(_new_page_alternative(sources.host, locale, book_ids))
    resolved = {
        target.raw_url
        for target in sources.legacy
        if target.target_kind == "drama" and target.locale == locale and sources.rs_ids.get(target.book_id, target.book_id) == canonical_id
    }
    legacy_urls = tuple(sorted(url for url in resolved if new_page.fullmatch(url) is None))
    matcher = re.compile(CHUNK_HEAD + "|".join(_alternatives(sources.host, locale, book_ids, legacy_urls)) + CHUNK_TAIL)
    return PageSet(sources.host, locale, book_ids, legacy_urls, skipped, matcher)
