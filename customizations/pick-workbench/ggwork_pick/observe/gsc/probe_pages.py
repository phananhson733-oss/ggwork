"""P4 and P7 of the GSC probe: how includingRegex matches, how long it may be and the Vh and Vd shapes; how GSC spells
page URLs (plan TR-07, D25; design 5.2, 5.5, 5.6).

P7 lists the pages of the last seven PT days and classifies each URL (gsc/urls.py). The host and path spelling of new
drama pages is what the D25 regex has to match: every new page must be matched by pageset.page_set's own anchored regex
as it stands, or the verdict is a fallback that says which spellings it misses.

P4 takes one identity as its seed, the new page with the most impressions in yesterday's C (or in P7's list), and
builds its page set P with pageset.page_set exactly as TR-23a will. D25's regex has two halves, and both are measured:
the new pages ([^/?#]+-<id>) and the legacy pages, whose raw strings are matched verbatim. P7's list has no snapshot to
resolve old pages with, so the most seen ?id= page and the most seen percent-encoded old page are borrowed into P as
if they resolved to the seed (LegacyTarget, same locale): whether GSC matches them as raw strings or as decoded text is
what decides whether the old pages (half the clicks) can be checked drama by drama at all.

Five filter requests settle the semantics: does P's anchored regex return exactly the pages page_set.contains accepts,
does a regex of the borrowed old pages alone return exactly those raw strings, does an unanchored id match part of a
URL, is ^ honoured, is a Perl-only lookahead refused. Either of the first two failing, or a partial match that ignores
^, decides against the D25 regex (不支持): TR-23a would compare a filter over other pages than the detail's. A ladder
and a bisection find the longest includingRegex GSC accepts, with filler alternatives shaped like real URLs that never
match. Vh and Vd are probe_vchecks.py's.
"""

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import timedelta

from ggwork_pick.observe.gsc.pageset import CHUNK_HEAD, CHUNK_TAIL, LegacyTarget, PageSet, PageSources, page_set, re2_escape
from ggwork_pick.observe.gsc.probe import (
    Answer,
    Finding,
    ProbeContext,
    Table,
    Verdict,
    ask,
    error_text,
    failed,
    filters,
    impressions,
    sum_by,
)
from ggwork_pick.observe.gsc.probe_vchecks import Part, shape_texts, vd, vh
from ggwork_pick.observe.gsc.query import GscQuery, GscRow
from ggwork_pick.observe.gsc.urls import UrlShape, classify_url

LADDER = (1024, 2048, 4096, 8192, 16384, 32768)
RESOLUTION = 64
CHUNK_MARGIN_PERCENT = 90  # TR-23a chunks at 90% of the longest regex GSC accepted
SEMANTICS_DAYS = 7
SHOWN_CHARS = 120
SHOWN_ROWS = 10
EXAMPLES = 2
FILLER_HEAD, FILLER_TAIL = "^(?:", ")$"
LEGACY_KINDS = frozenset({"legacy_detail", "legacy_video_play", "legacy_id_query"})
Carry = Mapping[str, object]

KIND_LABELS = {
    "new_drama": "新剧目页",
    "drama_nonstandard": "形似剧目页但不合形态",
    "new_watch": "新播放页",
    "legacy_detail": "旧站 /detail/",
    "legacy_video_play": "旧站 /video-play/",
    "legacy_id_query": "旧站 ?id=",
    "blog": "博客",
    "home": "首页",
    "other": "其他站内页",
    "foreign_host": "站外主机",
    "unparseable": "解析不了",
}


def _short(text: str) -> str:
    return text if len(text) <= SHOWN_CHARS else text[: SHOWN_CHARS - 1] + "…"


# ---- P7 ------------------------------------------------------------------------------------------------------------------


def _escape_case(new: list[UrlShape]) -> str | None:
    encoded = [shape for shape in new if shape.percent_encoded]
    lower = [shape for shape in encoded if shape.lowercase_escapes]
    if not encoded:
        return None
    return "upper" if not lower else ("lower" if len(lower) == len(encoded) else "mixed")


def _miss_reason(shape: UrlShape, site_host: str) -> str:
    reasons = (
        (shape.has_query, "带查询串"),
        (shape.has_fragment, "带片段"),
        (shape.scheme != "https", "非 https"),
        (shape.host != site_host, "主机不同"),
    )
    return next((text for holds, text in reasons if holds), "其他（locale 写法等）")


def _kind_table(pairs: list[tuple[UrlShape, GscRow]]) -> Table:
    def line(kind: str) -> tuple[str, ...]:
        members = [(shape, row) for shape, row in pairs if shape.kind == kind]
        examples = "；".join(_short(shape.url) for shape, _ in members[:EXAMPLES])
        with_query = sum(1 for shape, _ in members if shape.has_query)
        return (
            KIND_LABELS[kind],
            str(len(members)),
            str(impressions(row for _, row in members)),
            str(sum(row.clicks for _, row in members)),
            str(with_query),
            examples,
        )

    kinds = [kind for kind in KIND_LABELS if any(shape.kind == kind for shape, _ in pairs)]
    return Table("URL 种类（近 7 个 PT 日，[page]/all）", ("种类", "条数", "曝光", "点击", "带查询串", "例子"), tuple(line(kind) for kind in kinds))


def _host_table(pairs: list[tuple[UrlShape, GscRow]]) -> Table:
    counted = sum_by(pairs, lambda pair: f"{pair[0].scheme}://{pair[0].host}", lambda pair: 1)
    shown = sum_by(pairs, lambda pair: f"{pair[0].scheme}://{pair[0].host}", lambda pair: pair[1].impressions)
    new = sum_by(pairs, lambda pair: f"{pair[0].scheme}://{pair[0].host}", lambda pair: int(pair[0].kind == "new_drama"))
    rows = tuple((origin, str(counted[origin]), str(shown[origin]), str(new[origin])) for origin in sorted(counted, key=lambda origin: -counted[origin]))
    return Table("主机与协议", ("协议与主机", "条数", "曝光", "新剧目页条数"), rows)


def _p7_conclusion(ctx: ProbeContext, new: list[UrlShape], misses: list[UrlShape], hosts: list[str]) -> tuple[Verdict, str]:
    case = {"upper": "按百分号编码（大写十六进制）", "lower": "按百分号编码（小写十六进制）", "mixed": "百分号编码大小写混用", None: "都没有百分号编码"}[
        _escape_case(new)
    ]
    if not misses and hosts == [ctx.site_host]:
        return "支持", (
            f"{len(new)} 条新剧目页全部是 https://{ctx.site_host}/<locale>/drama/<slug>-<24 位 id>，都能被 page_set 的锚定正则整串匹配："
            f"D25 正则的主机部分写 {ctx.site_host}；非 ASCII 的 slug {case}"
        )
    reasons = sum_by(misses, lambda shape: _miss_reason(shape, ctx.site_host), lambda shape: 1)
    listed = "、".join(f"{reason} {count} 条" for reason, count in sorted(reasons.items()))
    return "退路", (
        f"新剧目页出现在 {len(hosts)} 个主机上（{'、'.join(hosts)}），{len(misses)} 条不合 page_set 的锚定形态（{listed or '无'}）："
        "D25 正则按现写法会漏掉这些页；TR-23a 要么把主机写成可选的 www.、要么把这些写法记为非标准页并计数"
    )


async def p7_page_urls(ctx: ProbeContext, carry: Carry) -> tuple[Finding, Carry]:
    """The pages of the last seven PT days, classified; new drama pages checked against page_set's regex."""
    answer = await ask(ctx, GscQuery(ctx.yesterday - timedelta(days=SEMANTICS_DAYS - 1), ctx.yesterday, ("page",), "all"))
    if not answer.ok:
        return failed("P7", "[page] 清单", answer.error), {}
    pairs = [(classify_url(row.keys[0], site_host=ctx.site_host), row) for row in answer.response.rows]
    new = [shape for shape, _ in pairs if shape.kind == "new_drama"]
    misses = [shape for shape in new if not shape.page_set_shape]
    hosts = sorted({shape.host for shape in new})
    tables = (_kind_table(pairs), _host_table(pairs))
    if misses:
        rows = tuple((_short(shape.url), _miss_reason(shape, ctx.site_host)) for shape in misses[:SHOWN_ROWS])
        tables = (*tables, Table("不合 page_set 形态的新剧目页（前 10 条）", ("URL", "原因"), rows))
    backfill = {
        "site_host": ctx.site_host,
        "new_page_hosts": hosts,
        "new_page_count": len(new),
        "new_page_shape_mismatch": len(misses),
        "slug_percent_encoded": any(shape.percent_encoded for shape in new),
        "percent_escape_case": _escape_case(new),
        "url_kinds": sum_by(pairs, lambda pair: pair[0].kind, lambda pair: 1),
    }
    carry_on = {"pages_7d": answer.response.rows}
    if not new:
        return Finding("P7", "未定", "近 7 个 PT 日没有新剧目页 URL，定不了主机写法", tables=tables, backfill=backfill), carry_on
    verdict, conclusion = _p7_conclusion(ctx, new, misses, hosts)
    facts = (("URL 条数", str(len(pairs))), ("满额", "是" if answer.response.truncated else "否"))
    return Finding("P7", verdict, conclusion, facts=facts, tables=tables, backfill=backfill), carry_on


# ---- the length of includingRegex ---------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LengthSearch:
    max_ok: int | None  # the longest length accepted
    min_fail: int | None  # the shortest length refused (None: nothing tried was refused)
    attempts: tuple[tuple[int, bool | None], ...]
    aborted: bool  # an answer was neither accepted nor refused, so the search stopped


async def search_limit(accepts: Callable[[int], Awaitable[bool | None]], *, ladder=LADDER, resolution: int = RESOLUTION) -> LengthSearch:
    """Climb the ladder to the first refusal, then bisect until the bracket is at most `resolution` wide.
    accepts(length) is True (200), False (a 400) or None (anything else: the search stops there)."""
    attempts, best, failing = (), 0, None
    for length in ladder:
        outcome = await accepts(length)
        attempts = (*attempts, (length, outcome))
        if outcome is None:
            return LengthSearch(best or None, None, attempts, True)
        if not outcome:
            failing = length
            break
        best = length
    if failing is None:
        return LengthSearch(best or None, None, attempts, False)
    low, high = best, failing
    while high - low > resolution:
        middle = (low + high) // 2
        outcome = await accepts(middle)
        attempts = (*attempts, (middle, outcome))
        if outcome is None:
            return LengthSearch(low or None, high, attempts, True)
        low, high = (middle, high) if outcome else (low, middle)
    return LengthSearch(low or None, high, attempts, False)


def filler_regex(host: str, length: int) -> str:
    """An anchored alternation of exactly `length` characters, shaped like a page set's chunk, that matches no real page
    (the /zz/ locale does not exist)."""
    room = length - len(FILLER_HEAD) - len(FILLER_TAIL)
    if room < 1:
        raise ValueError(f"length {length} leaves no room for an alternative")
    unit = f"https://{re2_escape(host)}/zz/drama/probe-"
    count = (room + 1) // (len(unit) + 6 + 1)
    body = "|".join(f"{unit}{number:06d}" for number in range(count))
    return FILLER_HEAD + body + "z" * (room - len(body)) + FILLER_TAIL


async def _accepts(ctx: ProbeContext, length: int) -> bool | None:
    groups = filters("page", "includingRegex", filler_regex(ctx.site_host, length))
    answer = await ask(ctx, GscQuery(ctx.yesterday, ctx.yesterday, ("date",), "all", dimension_filter_groups=groups))
    if answer.ok:
        return True
    return False if answer.error.kind == "bad_request" else None


# ---- P4 ------------------------------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Seed:
    url: str
    pset: PageSet

    @property
    def regex(self) -> str:
        (chunk,) = self.pset.regex_chunks(LADDER[-1]).chunks
        return chunk

    @property
    def legacy_regex(self) -> str | None:
        """The borrowed old pages alone, as page_set renders a legacy alternative (verbatim, escaped for RE2)."""
        urls = self.pset.legacy_urls
        return CHUNK_HEAD + "|".join(re2_escape(url) for url in urls) + CHUNK_TAIL if urls else None

    @property
    def book_id(self) -> str:
        return self.pset.book_ids[0]


def _page_totals(carry: Carry, yesterday) -> tuple[dict[str, int], ...]:
    detail = (carry.get("c_responses") or {}).get(yesterday)
    from_c = sum_by(detail.rows, lambda row: row.keys[1], lambda row: row.impressions) if detail is not None else {}
    return from_c, {row.keys[0]: row.impressions for row in carry.get("pages_7d", ())}


def _legacy_samples(ctx: ProbeContext, carry: Carry) -> tuple[str, ...]:
    """The most seen ?id= page and the most seen percent-encoded old page of P7's list (one URL when they coincide)."""
    listed = sorted(carry.get("pages_7d", ()), key=lambda row: (-row.impressions, row.keys[0]))
    shapes = [classify_url(row.keys[0], site_host=ctx.site_host) for row in listed]
    id_query = next((shape.url for shape in shapes if shape.kind == "legacy_id_query"), None)
    encoded = next((shape.url for shape in shapes if shape.kind in LEGACY_KINDS and shape.percent_encoded), None)
    return tuple(dict.fromkeys(url for url in (id_query, encoded) if url is not None))


def _seed(ctx: ProbeContext, carry: Carry) -> Seed | None:
    """The new page with the most impressions whose URL page_set's regex matches as it stands, with P7's sample old
    pages borrowed into its page set."""
    ranked = (url for totals in _page_totals(carry, ctx.yesterday) for url in sorted(totals, key=lambda url: (-totals[url], url)))
    shape = next((shape for shape in (classify_url(url, site_host=ctx.site_host) for url in ranked) if shape.page_set_shape), None)
    if shape is None:
        return None
    page = shape.new_page
    legacy = tuple(LegacyTarget(url, "drama", page.locale, page.book_id) for url in _legacy_samples(ctx, carry))
    sources = PageSources(host=ctx.site_host, rs_ids={}, legacy=legacy)
    return Seed(shape.url, page_set(canonical_id=page.book_id, locale=page.locale, sources=sources))


def _outcome(answer: Answer) -> str:
    return f"{len(answer.response.rows)} 行" if answer.ok else f"HTTP {answer.error.status}（{answer.error.kind}）"


def _returned(answer: Answer) -> set[str] | None:
    return {row.keys[0] for row in answer.response.rows} if answer.ok else None


def _listing(urls, limit: int = EXAMPLES) -> str:
    ordered = sorted(urls)
    more = f" 等 {len(ordered)} 条" if len(ordered) > limit else ""
    return "、".join(_short(url) for url in ordered[:limit]) + more


def _semantic_values(seed: Seed, carry: Carry, answers: Mapping[str, Answer]) -> tuple[dict[str, object], tuple[str, ...]]:
    """The semantics backfill, and the findings that decide against the D25 regex."""
    listed = {row.keys[0] for row in carry.get("pages_7d", ())}
    expected = {url for url in listed if seed.pset.contains(url)} or {seed.url}
    exact, legacy = _returned(answers["exact"]), _returned(answers["legacy"]) if "legacy" in answers else None
    partial, anchored, lookahead = answers["partial"], answers["anchored"], answers["lookahead"]
    values = {
        "regex_page_set_exact": None if exact is None else expected <= exact and all(seed.pset.contains(url) for url in exact),
        "regex_legacy_exact": None if legacy is None else legacy == set(seed.pset.legacy_urls),
        "regex_partial_match": (seed.url in _returned(partial)) if partial.ok else None,
        "regex_anchor_honored": (not anchored.response.rows) if anchored.ok else None,
        "regex_re2_only": False if lookahead.ok else (True if lookahead.error.kind == "bad_request" else None),
    }
    problems = (
        *((f"旧页原串没有按原串匹配（缺 {_listing(set(seed.pset.legacy_urls) - legacy)}；GSC 可能按解码后的文本匹配）：旧页按原串精确查表的逐剧核对不成立",)
          if values["regex_legacy_exact"] is False else ()),
        *((f"page_set 的锚定正则返回的页面与 page_set.contains 不一致（缺 {_listing(expected - exact) or '无'}，"
           f"多出 {_listing({url for url in exact if not seed.pset.contains(url)}) or '无'}）：过滤请求与明细不是同一个页面集合",)
          if values["regex_page_set_exact"] is False else ()),
        *(("部分匹配而 ^ 不生效：锚定正则挡不住多出的页面，过滤请求会数进别的剧",)
          if values["regex_partial_match"] and values["regex_anchor_honored"] is False else ()),
    )  # fmt: skip
    return values, problems


def _cases(seed: Seed) -> tuple[tuple[str, str, str], ...]:
    legacy = (("legacy", "旧页原串（借来的）", seed.legacy_regex),) if seed.legacy_regex else ()
    return (
        ("exact", "page_set 的锚定正则", seed.regex),
        *legacy,
        ("partial", "未锚定的 id", seed.book_id),
        ("anchored", "^ 加 id", "^" + seed.book_id),
        ("lookahead", "前瞻（RE2 不支持）", "(?=https)" + seed.regex),
    )


async def _semantics(ctx: ProbeContext, seed: Seed, carry: Carry) -> Part:
    days = (ctx.yesterday - timedelta(days=SEMANTICS_DAYS - 1), ctx.yesterday)
    cases = _cases(seed)
    answers = {
        key: await ask(ctx, GscQuery(*days, ("page",), "all", dimension_filter_groups=filters("page", "includingRegex", expression)))
        for key, _, expression in cases
    }
    values, problems = _semantic_values(seed, carry, answers)
    rows = tuple((name, _short(expression), _outcome(answers[key])) for key, name, expression in cases)
    table = Table("includingRegex 的语义（近 7 个 PT 日，[page]/all）", ("用例", "表达式", "结果"), rows)
    exact = answers["exact"]
    refused = "page_set 的锚定正则被拒" if not exact.ok and exact.error.kind == "bad_request" else None
    unclear = (
        *((exact,) if not exact.ok and refused is None else ()),
        *(answers[key] for key in ("legacy", "partial", "anchored") if key in answers and not answers[key].ok),
        *((answers["lookahead"],) if values["regex_re2_only"] is None else ()),
    )
    return Part((table,), values, next(iter(unclear), None), refused, problems=problems)


async def _length(ctx: ProbeContext) -> tuple[Part, LengthSearch]:
    found = await search_limit(lambda length: _accepts(ctx, length))
    suggested = found.max_ok * CHUNK_MARGIN_PERCENT // 100 if found.max_ok else None
    backfill = {"regex_max_length_ok": found.max_ok, "regex_min_length_rejected": found.min_fail, "regex_chunk_length_suggested": suggested}
    rows = tuple((str(length), {True: "接受", False: "400 拒绝", None: "未得到明确回答"}[outcome]) for length, outcome in found.attempts)
    return Part((Table("includingRegex 长度（梯度加二分）", ("长度", "结果"), rows),), backfill), found


def _p4_text(backfill: Mapping[str, object], found: LengthSearch) -> str:
    match = "按 RE2 部分匹配（未锚定的 id 也命中），正则必须锚定" if backfill["regex_partial_match"] else "按整串匹配（未锚定的 id 不命中）"
    anchor = "^ 生效" if backfill["regex_anchor_honored"] else "^ 不生效"
    re2 = "前瞻被拒，只认 RE2 语法" if backfill["regex_re2_only"] else "前瞻被接受，不像 RE2"
    exact = (
        "page_set 的锚定正则返回的页面与 page_set.contains 相符"
        if backfill["regex_page_set_exact"]
        else "page_set 的锚定正则返回的页面与 page_set.contains 不一致"
    )
    legacy = {True: "旧页原串按原串精确匹配", False: "旧页原串没有按原串精确匹配", None: "P7 清单里没有旧页，旧页原串的匹配未测"}[
        backfill["regex_legacy_exact"]
    ]
    return "；".join((f"includingRegex {match}；{anchor}；{re2}；{exact}；{legacy}；{_length_text(backfill, found)}", *shape_texts(backfill)))


def _length_text(backfill: Mapping[str, object], found: LengthSearch) -> str:
    if found.max_ok is None:
        return f"连 {found.min_fail} 个字符的正则都被拒"
    bound = f"上限在 {found.max_ok} 与 {found.min_fail} 之间" if found.min_fail else f"试到 {found.max_ok} 仍被接受"
    return f"长度{bound}，TR-23a 分块建议不超过 {backfill['regex_chunk_length_suggested']}"


def _seed_facts(seed: Seed) -> tuple[tuple[str, str], ...]:
    borrowed = "；".join(seed.pset.legacy_urls) or "无（P7 清单里没有旧页）"
    return (
        ("种子页", seed.url),
        ("种子 book_id", seed.book_id),
        ("借来测正则的旧页原串", borrowed),
        ("说明", "旧页原串是从 P7 清单借来测正则的，不是这部剧真实的旧页；Vh、Vd 的两边都按同一个页面集合带上了它们"),
        ("page_set 正则", seed.regex),
    )


def _p4_verdict(parts: tuple[Part, ...], found: LengthSearch, backfill: Mapping[str, object], vd_part: Part) -> tuple[Verdict, str]:
    refused = next((part.refused for part in parts if part.refused), None)
    broken = next((part.broken for part in parts if part.broken is not None), None)
    problems = tuple(problem for part in parts for problem in part.problems)
    if refused:
        return "不支持", f"{refused}：用到它的逐剧核对做不了，相关的剧只出描述性标签"
    if broken is not None or found.aborted:
        return "未定", f"有请求没有得到明确回答（{error_text(broken.error) if broken else '长度搜索中断'}），再跑一次"
    if problems:
        return "不支持", "；".join((*problems, "交 G2 决定 TR-23a 的正则怎么改", _p4_text(backfill, found)))
    if vd_part.fallback:
        return "退路", f"{vd_part.fallback}；{_p4_text(backfill, found)}"
    return "支持", _p4_text(backfill, found)


async def p4_regex(ctx: ProbeContext, carry: Carry) -> tuple[Finding, Carry]:
    seed = _seed(ctx, carry)
    if seed is None:
        return Finding("P4", "未定", "没有可作种子的新剧目页（C 与 P7 里都没有合 page_set 形态的新页）：P4 无从测起"), {}
    semantics = await _semantics(ctx, seed, carry)
    length, found = await _length(ctx)
    vh_part, vd_part = await vh(ctx, seed.pset, seed.regex, carry), await vd(ctx, seed.pset, seed.regex)
    parts = (semantics, length, vh_part, vd_part)
    backfill = {name: value for part in parts for name, value in part.backfill.items()}
    tables = tuple(table for part in parts for table in part.tables)
    verdict, conclusion = _p4_verdict(parts, found, backfill, vd_part)
    return Finding("P4", verdict, conclusion, facts=_seed_facts(seed), tables=tables, backfill=backfill), {}
