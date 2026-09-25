"""TR-09: the page set P of one identity (plan D25, section 2 premise 3; design 5.5).

P comes from the identity and the round's frozen inputs only: the canonical book id, the frozen mirror version's rs_ids
and the frozen legacy snapshot. It never reads C, D or E. The detail sum X_det and the filter requests' includingRegex
both come from this one P, so a drama whose pages are all missing from the detail is still asked for by name
(counterexample 22).
"""

import re

import pytest
from gsc_builders import H_C, W0, W1, compare

from ggwork_pick.observe.gsc import coverage, pageset

HOST = "dramashortstv.com"
CANON = "650a1b2c3d4e5f6a7b8c9d0e"
NONCANON = "650a1b2c3d4e5f6a7b8c9dff"
OTHER = "111111111111111111111111"
NEW_PAGE = f"https://{HOST}/en/drama/the-alphas-bride-{CANON}"
NONCANON_PAGE = f"https://{HOST}/en/drama/the-alpha-s-bride-{NONCANON}"
LEGACY = f"https://{HOST}/en?id=38000"
LEGACY_VIA_RS_IDS = f"https://{HOST}/old/the-alphas-bride"
LEGACY_OTHER = f"https://{HOST}/en?id=49020"
SOURCES = pageset.PageSources(
    host=HOST,
    rs_ids={CANON: CANON, NONCANON: CANON, OTHER: OTHER, f"{CANON}(dub)": CANON},
    legacy=(
        pageset.LegacyTarget(LEGACY, "drama", "en", CANON),
        pageset.LegacyTarget(LEGACY_OTHER, "drama", "en", OTHER),
        pageset.LegacyTarget(LEGACY_VIA_RS_IDS, "drama", "en", NONCANON),
        pageset.LegacyTarget(f"https://{HOST}/es?id=38000", "drama", "es", CANON),
        pageset.LegacyTarget(f"https://{HOST}/en?id=12", "blog", "en", None),
        pageset.LegacyTarget(f"https://{HOST}/en?id=13", "unresolved", None, None),
        pageset.LegacyTarget(NEW_PAGE, "drama", "en", CANON),  # already a new page: covered by the new-page pattern
    ),
)
MEMBERS = (NEW_PAGE, NONCANON_PAGE, LEGACY, LEGACY_VIA_RS_IDS)
STRANGERS = (
    f"https://{HOST}/en/drama/other-{OTHER}",
    f"https://{HOST}/es/drama/the-alphas-bride-{CANON}",
    f"https://www.{HOST}/en/drama/the-alphas-bride-{CANON}",
    f"http://{HOST}/en/drama/the-alphas-bride-{CANON}",
    f"{NEW_PAGE}?utm=x",
    f"{NEW_PAGE}/episodes",
    f"https://{HOST}/en/drama/{CANON}",
    LEGACY_OTHER,
    f"{LEGACY}0",
    f"{LEGACY}&page=2",
    f"{LEGACY}\n",
    f"https://{HOST}/es?id=38000",
    f"https://{HOST}/en?id=12",
    f"https://{HOST}/enxid=38000",
    f"https://other.example/?next={NEW_PAGE}",  # a member inside another URL: only a match anchored at the start refuses it
    f"https://other.example/?next={LEGACY}",
)


def _members(**overrides) -> pageset.PageSet:
    return pageset.page_set(**{"canonical_id": CANON, "locale": "en", "sources": SOURCES, **overrides})


def _as_re2(chunk: str) -> re.Pattern:
    """How GSC reads an includingRegex chunk: RE2 matches anywhere in the URL (a search, not a full match) and its $ is
    the end of the text only, which is Python's \\Z (Python's $ also matches before a final newline)."""
    return re.compile(chunk[:-1] + r"\Z" if chunk.endswith(")$") else chunk)


def _chunk_hits(plan: pageset.RegexPlan, url: str) -> int:
    return sum(1 for chunk in plan.chunks if _as_re2(chunk).search(url))


def test_page_set_members():
    members = _members()
    assert members.book_ids == (CANON, NONCANON)
    assert members.legacy_urls == tuple(sorted((LEGACY, LEGACY_VIA_RS_IDS)))
    assert members.skipped_ids == (f"{CANON}(dub)",)
    assert all(members.contains(url) for url in MEMBERS)
    assert not any(members.contains(url) for url in STRANGERS)
    new_page, *legacy = members.alternatives
    assert new_page == f"https://dramashortstv\\.com/en/drama/[^/?#]+-(?:{CANON}|{NONCANON})"
    assert tuple(legacy) == tuple(pageset.re2_escape(url) for url in members.legacy_urls)


def test_consistency_same_pageset():
    """X_det and the filter regex come from the same P: tampering with either side turns this red."""
    members = _members()
    for limit in (4096, 140):
        plan = members.regex_chunks(limit)
        assert plan.complete and plan.chunks
        for url in MEMBERS + STRANGERS:
            assert _chunk_hits(plan, url) == (1 if members.contains(url) else 0), url
    rows = tuple(coverage.GscRow(country="USA", impressions=n, clicks=0, page=url, hour=H_C - W0.span / 2) for n, url in enumerate(MEMBERS + STRANGERS, 1))
    plan = members.regex_chunks(140)
    by_regex = sum(row.impressions for row in rows if _chunk_hits(plan, row.page))
    assert coverage.detail_value(rows, members, W0, "USA").value == by_regex == sum(range(1, len(MEMBERS) + 1))


def test_pageset_independent_of_detail():
    """Counterexample 22: the drama has no row at all in C; its regex still asks for its new and legacy pages."""
    members = _members()
    stranger_page = STRANGERS[0]
    c_rows = tuple(coverage.GscRow(country="USA", impressions=50, clicks=2, page=stranger_page, hour=H_C - W0.span / 2 - W0.span * k) for k in (0, 1))
    plan = members.regex_chunks(4096)
    for url in MEMBERS:
        assert _chunk_hits(plan, url) == 1, url
    # What the filter request sees (GSC's own rows, not the detail we received): the drama's pages did get impressions.
    truth = c_rows + tuple(
        coverage.GscRow(country="USA", impressions=15, clicks=0, page=url, hour=H_C - W0.span / 2 - W0.span * k) for url in MEMBERS for k in (0, 1)
    )
    filtered = {w.label: sum(r.impressions for r in truth if _chunk_hits(plan, r.page) and w.covers_hour(r.hour)) for w in (W0, W1)}
    assert filtered == {"w0": 60, "w_minus_1": 60}
    for window in (W0, W1):
        x_det = coverage.detail_value(c_rows, members, window, "USA")
        assert (x_det.value, x_det.row_count) == (None, 0)
        got = compare(window, None, filtered[window.label])
        assert (got.admitted, got.reason) == (False, "detail_gap")
    # P collected from the detail rows instead would be empty for this drama and ask GSC for nothing.
    from_detail = {row.page for row in c_rows if members.contains(row.page)}
    assert from_detail == set() and not all(url in from_detail for url in MEMBERS)


def test_regex_chunks_split_and_overflow():
    many = pageset.PageSources(
        host=HOST,
        rs_ids={},
        legacy=tuple(pageset.LegacyTarget(f"https://{HOST}/en?id={n}", "drama", "en", CANON) for n in range(40000, 40030)),
    )
    members = pageset.page_set(canonical_id=CANON, locale="en", sources=many)
    plan = members.regex_chunks(200)
    assert plan.complete and len(plan.chunks) > 1
    assert all(len(chunk) <= 200 and chunk.startswith("^(?:") and chunk.endswith(")$") for chunk in plan.chunks)
    for url in (NEW_PAGE, *members.legacy_urls):
        assert _chunk_hits(plan, url) == 1, url

    too_long = f"https://{HOST}/en?id=" + "9" * 300
    long_sources = pageset.PageSources(host=HOST, rs_ids={}, legacy=(pageset.LegacyTarget(too_long, "drama", "en", CANON),))
    overflowing = pageset.page_set(canonical_id=CANON, locale="en", sources=long_sources).regex_chunks(200)
    assert not overflowing.complete and overflowing.overflow == (pageset.re2_escape(too_long),)
    assert _chunk_hits(overflowing, NEW_PAGE) == 1 and _chunk_hits(overflowing, too_long) == 0
    with pytest.raises(ValueError):
        members.regex_chunks(6)  # not even the anchored wrapper fits


def test_regex_is_re2_safe():
    odd = f"https://{HOST}/en/(old)+drama.html?id=1&x=a|b[c]{{2}}^$*\\"
    sources = pageset.PageSources(host=HOST, rs_ids={}, legacy=(pageset.LegacyTarget(odd, "drama", "en", CANON),))
    plan = pageset.page_set(canonical_id=CANON, locale="en", sources=sources).regex_chunks(4096)
    assert _chunk_hits(plan, odd) == 1 and _chunk_hits(plan, odd.replace(".html", "xhtml")) == 0
    assert pageset.re2_escape("a.b?c(d)+e|f[g]{h}^$*\\-#& ~") == "a\\.b\\?c\\(d\\)\\+e\\|f\\[g\\]\\{h\\}\\^\\$\\*\\\\-#& ~"
    for chunk in plan.chunks:  # RE2 has no lookaround, backreference or named-group syntax of Python's
        assert not re.search(r"\(\?[=!<P]|\\[1-9]|\\Z", chunk)


def test_page_set_refuses_bad_inputs():
    for bad in ({"canonical_id": "not-hex"}, {"canonical_id": CANON.upper()}, {"locale": "EN"}, {"locale": "en/x"}):
        with pytest.raises(ValueError):
            _members(**bad)
    with pytest.raises(ValueError):
        pageset.page_set(canonical_id=CANON, locale="en", sources=pageset.PageSources(host="dramashortstv.com/x", rs_ids={}, legacy=()))
