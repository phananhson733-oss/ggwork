"""TR-07: page URLs as GSC returns them (P7), and the title key that finds positive controls (plan TR-07; design 5.5).

The shapes are the ones RealShort's own parser knows (rs:src/lib/gsc-url.ts, rs = realshort-pick-export-v2 816ca2e) and
the ones the TR-08 fixtures list; the title key is RealShort's titleToSlug without its 60-codepoint cut
(rs:src/lib/slug.ts), so a query compares with a slug the way RealShort's import compares it with a title.
"""

import pytest
from gsc_sim import BG, BG_SLUG, DETAIL, EN, HOME, ID_38000, ID_49020, ID_EN, WWW_PLAY

from ggwork_pick.observe.gsc import urls
from ggwork_pick.observe.gsc.pageset import PageSources, page_set

SITE = "dramashortstv.com"


def shape(url: str) -> urls.UrlShape:
    return urls.classify_url(url, site_host=SITE)


def test_title_key_follows_realshort_slug_rules():
    assert urls.title_key("The Billionaire's Secret Wife") == "the-billionaires-secret-wife"
    assert urls.title_key("The Billionaire’s Secret Wife") == "the-billionaires-secret-wife"  # curly apostrophe, dropped too
    assert urls.title_key("  Don`t -- Stop!!  ") == "dont-stop"
    assert urls.title_key("Великият и могъщ джин") == "великият-и-могъщ-джин"
    assert urls.title_key("Café Love") == urls.title_key("Café love") == "café-love"  # NFC, as RealShort compares
    assert urls.title_key("แม่บ้านจำเป็น") == "แม่บ้านจำเป็น"  # combining marks are kept, not cut out
    assert urls.title_key("!!!") == ""


def test_slug_title_key_decodes_only_to_compare():
    assert urls.slug_title_key(BG_SLUG) == urls.title_key("великият и могъщ джин")
    assert urls.slug_title_key("the-billionaires-secret-wife") == "the-billionaires-secret-wife"
    # An escape that is not UTF-8 is left as it is (rs gsc-url.ts segments()), then keyed like any other text.
    assert urls.slug_title_key("abc-%E0%A4") == "abc-e0-a4"


def test_site_host_of_both_property_kinds():
    assert urls.site_host_of("sc-domain:dramashortstv.com") == "dramashortstv.com"
    assert urls.site_host_of("https://dramashortstv.com/") == "dramashortstv.com"
    assert urls.site_host_of("https://www.example.com/sub/") == "www.example.com"


def test_new_page_parts():
    page = urls.parse_new_page(EN)
    assert (page.host, page.locale, page.slug, page.book_id) == (SITE, "en", "the-billionaires-secret-wife", ID_EN)
    assert urls.parse_new_page(BG).slug == BG_SLUG  # verbatim: decoding is only for comparing with a query
    assert urls.parse_new_page(EN + "?utm_source=x").book_id == ID_EN  # the path decides; the query string is flagged
    for other in (HOME, DETAIL, EN + "/", EN.replace(ID_EN, ID_EN.upper()), "not a url", f"https://{SITE}/en/drama/{ID_EN}"):
        assert urls.parse_new_page(other) is None


@pytest.mark.parametrize(
    "url, kind",
    [
        (EN, "new_drama"),
        (BG, "new_drama"),
        (f"https://{SITE}/zh-TW/drama/x-{ID_EN}", "new_drama"),
        (EN + "/", "drama_nonstandard"),
        (EN.replace(ID_EN, ID_EN.upper()), "drama_nonstandard"),
        (f"https://{SITE}/en/watch/the-billionaires-secret-wife/1", "new_watch"),
        (DETAIL, "legacy_detail"),
        (f"https://{SITE}/bg/detail/38000?id=38000", "legacy_detail"),
        (WWW_PLAY, "legacy_video_play"),
        (ID_38000, "legacy_id_query"),
        (ID_49020, "legacy_id_query"),
        (f"https://{SITE}/for-you?id=72142", "legacy_id_query"),
        (HOME, "home"),
        (f"https://{SITE}/en", "home"),
        (f"https://{SITE}/zh-TW/", "home"),
        (f"https://{SITE}/blog/mighty-and-great-genie", "blog"),
        (f"https://{SITE}/en/blog/x", "blog"),
        (f"https://{SITE}/about", "other"),
        (f"https://example.com/en/drama/x-{ID_EN}", "foreign_host"),
        ("not a url", "unparseable"),
        ("ftp://dramashortstv.com/x", "unparseable"),
    ],
)
def test_classify_url_kinds(url, kind):
    assert shape(url).kind == kind


def test_classify_url_flags():
    plain = shape(EN)
    assert (plain.scheme, plain.host, plain.page_set_shape) == ("https", SITE, True)
    assert not (plain.has_query or plain.has_fragment or plain.percent_encoded or plain.non_ascii or plain.trailing_slash)
    encoded = shape(BG)
    assert encoded.percent_encoded and not encoded.lowercase_escapes and encoded.page_set_shape
    assert shape(BG.lower().replace("dramashortstv.com", SITE)).lowercase_escapes
    raw = shape(f"https://{SITE}/bg/drama/великият-{ID_EN}")
    assert raw.kind == "new_drama" and raw.non_ascii and raw.page_set_shape
    # New pages the anchored D25 regex would miss as it stands: a query string, another host, plain http.
    for missed in (EN + "?utm_source=x", EN.replace(SITE, "www." + SITE), EN.replace("https:", "http:"), EN + "#top"):
        found = shape(missed)
        assert found.kind == "new_drama" and not found.page_set_shape
    assert shape(EN + "?utm_source=x").has_query and shape(EN + "#top").has_fragment
    assert shape(EN.replace(SITE, "www." + SITE)).host == "www." + SITE
    assert shape(f"https://{SITE}/en/").trailing_slash and not shape(HOME).trailing_slash
    assert shape(DETAIL).page_set_shape is False and shape(DETAIL).new_page is None


def test_page_set_shape_is_the_d25_regex():
    """page_set_shape is decided by pageset.page_set itself, so P7 measures the regex TR-23a will send."""
    for url in (EN, BG, EN + "?x=1", EN.replace(SITE, "www." + SITE)):
        found = shape(url)
        pset = page_set(canonical_id=found.new_page.book_id, locale=found.new_page.locale, sources=PageSources(host=SITE, rs_ids={}, legacy=()))
        assert found.page_set_shape is pset.contains(url)
