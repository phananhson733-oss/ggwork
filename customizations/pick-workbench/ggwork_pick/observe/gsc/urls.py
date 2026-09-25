"""Page URLs as GSC returns them: their shape (TR-07 P7), the parts of a new drama page, and the title key the
positive-control candidates compare queries with (plan TR-07; design 5.5, 4.11).

Nothing here decides attribution: the workbench matches raw URL strings exactly and never decodes them (design 5.5,
TR-22). This module only describes the strings, so that P7 can say how GSC spells the host and paths the D25 regex has
to match, and so that a slug can be compared with a query the way RealShort's own GSC import compares a title with one
(rs = realshort-pick-export-v2 816ca2e: the path segments of rs:src/lib/gsc-url.ts:48-66, the title rule of
rs:src/lib/slug.ts titleToSlug without its 60-codepoint cut, the page kinds of gsc-url.ts:38-41).

page_set_shape asks pageset.page_set itself whether its anchored regex matches the URL as it stands, so P7 measures
the very regex TR-23a will send.
"""

import re
import unicodedata
from dataclasses import dataclass
from typing import Literal
from urllib.parse import parse_qs, unquote, urlsplit

from ggwork_pick.observe.gsc.pageset import PageSources, page_set

UrlKind = Literal[
    "new_drama", "new_watch", "legacy_detail", "legacy_video_play", "legacy_id_query", "blog", "home", "drama_nonstandard", "other",
    "foreign_host", "unparseable",
]  # fmt: skip

_NEW_PAGE = re.compile(r"^/(?P<locale>[^/]+)/drama/(?P<slug>[^/?#]+)-(?P<book_id>[0-9a-f]{24})$")
_LOCALE_ONLY = re.compile(r"^/[a-z]{2}(?:-[A-Za-z]{2,4})?/?$")
_BLOG = re.compile(r"^/(?:[a-z]{2}(?:-[A-Za-z]{2,4})?/)?blog(?:/|$)")
_ESCAPE = re.compile(r"%[0-9A-Fa-f]{2}")
_APOSTROPHES = re.compile("['’‘`]")  # dropped, not hyphenated: "don't" -> "dont" (rs slug.ts)
_HYPHENS = re.compile(r"-+")
_LEGACY_SEGMENTS = {"detail": "legacy_detail", "video-play": "legacy_video_play"}
_DOMAIN_PREFIX = "sc-domain:"


@dataclass(frozen=True, slots=True)
class NewPage:
    """/<locale>/drama/<slug>-<book_id>, with the slug verbatim (still percent-encoded when GSC sends it so)."""

    host: str
    locale: str
    slug: str
    book_id: str


@dataclass(frozen=True, slots=True)
class UrlShape:
    url: str
    kind: UrlKind
    scheme: str
    host: str
    has_query: bool
    has_fragment: bool
    percent_encoded: bool
    lowercase_escapes: bool  # at least one %xx escape spelled with lowercase hex
    non_ascii: bool
    trailing_slash: bool
    page_set_shape: bool  # a new drama page the D25 regex (pageset.page_set) matches as it stands
    new_page: NewPage | None


def site_host_of(site_url: str) -> str:
    """The host a property's pages live on: sc-domain:x is x, a URL-prefix property is its host."""
    if site_url.startswith(_DOMAIN_PREFIX):
        return site_url[len(_DOMAIN_PREFIX) :].lower()
    return (urlsplit(site_url).hostname or "").lower()


def title_key(text: str) -> str:
    """RealShort's title-to-slug rule without the length cut: lowercase, apostrophes dropped, NFC, every run of anything
    but letters, digits and combining marks turned into one hyphen, hyphens trimmed at both ends."""
    normalized = unicodedata.normalize("NFC", _APOSTROPHES.sub("", text.lower()))
    kept = "".join(char if unicodedata.category(char)[0] in "LNM" else "-" for char in normalized)
    return _HYPHENS.sub("-", kept).strip("-")


def slug_title_key(slug: str) -> str:
    """A slug's title key: percent-decoded to compare it with a query (never to attribute it); an escape that is not
    UTF-8 stays as it is, as rs gsc-url.ts:55-64 leaves it."""
    try:
        decoded = unquote(slug, errors="strict")
    except UnicodeDecodeError:
        decoded = slug
    return title_key(decoded)


def _split(url: str):
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    return parts if parts.scheme in ("http", "https") and parts.hostname else None


def parse_new_page(url: str) -> NewPage | None:
    """The parts of a new drama page, decided by the path alone (a query string or another host is P7's to flag)."""
    parts = _split(url)
    matched = _NEW_PAGE.fullmatch(parts.path) if parts is not None else None
    if matched is None:
        return None
    return NewPage(parts.hostname.lower(), matched["locale"], matched["slug"], matched["book_id"])


def _matches_page_set(url: str, page: NewPage, site_host: str) -> bool:
    try:
        pset = page_set(canonical_id=page.book_id, locale=page.locale, sources=PageSources(host=site_host, rs_ids={}, legacy=()))
    except ValueError:  # a locale page_set does not take: the regex could never be built for it
        return False
    return pset.contains(url)


def _kind(parts, site_host: str, page: NewPage | None) -> UrlKind:
    host = parts.hostname.lower()
    if host not in (site_host, f"www.{site_host}"):
        return "foreign_host"
    if page is not None:
        return "new_drama"
    segments = [segment for segment in parts.path.split("/") if segment]
    legacy = next((_LEGACY_SEGMENTS[segment] for segment in segments if segment in _LEGACY_SEGMENTS), None)
    if legacy is not None:
        return legacy
    if "id" in parse_qs(parts.query):
        return "legacy_id_query"
    if len(segments) >= 2 and segments[1] == "watch":
        return "new_watch"
    if _BLOG.match(parts.path):
        return "blog"
    if parts.path in ("", "/") or _LOCALE_ONLY.fullmatch(parts.path):
        return "home"
    return "drama_nonstandard" if "drama" in segments else "other"


def classify_url(url: str, *, site_host: str) -> UrlShape:
    """What kind of page a GSC URL is and how it is spelled; site_host is the property's (www. counts as the site)."""
    parts = _split(url)
    if parts is None:
        return UrlShape(url, "unparseable", "", "", False, False, False, False, not url.isascii(), False, False, None)
    page = parse_new_page(url)
    escapes = _ESCAPE.findall(url)
    return UrlShape(
        url=url,
        kind=_kind(parts, site_host, page),
        scheme=parts.scheme,
        host=parts.hostname.lower(),
        has_query=bool(parts.query) or "?" in url,
        has_fragment=bool(parts.fragment) or "#" in url,
        percent_encoded=bool(escapes),
        lowercase_escapes=any(escape != escape.upper() for escape in escapes),
        non_ascii=not url.isascii(),
        trailing_slash=len(parts.path) > 1 and parts.path.endswith("/"),
        page_set_shape=page is not None and _matches_page_set(url, page, site_host),
        new_page=page,
    )
