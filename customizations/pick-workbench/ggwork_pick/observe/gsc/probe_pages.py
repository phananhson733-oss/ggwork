"""P4 and P7 of the GSC probe: how includingRegex matches, how long it may be and the Vh and Vd shapes; how GSC spells
page URLs (plan TR-07, D25; design 5.2, 5.5, 5.6).

P7 lists the pages of the last seven PT days and classifies each URL (gsc/urls.py). The host and path spelling of new
drama pages is what the D25 regex has to match: every new page must be matched by pageset.page_set's own anchored regex
as it stands, or the verdict is a fallback that says which spellings it misses.

P4 takes one identity as its seed, the new page with the most impressions in yesterday's C (or in P7's list), and
builds its page set P with pageset.page_set exactly as TR-23a will. With it: four filter requests settle the regex
semantics (does P's anchored regex return exactly the pages page_set.contains accepts, does an unanchored id match part
of a URL, is ^ honoured, is a Perl-only lookahead refused); a ladder and a bisection find the longest includingRegex
GSC accepts, with filler alternatives shaped like real URLs that never match; Vh ([hour,country]) and Vd
([date,country], all and final) are sent with P's regex and compared cell by cell with the detail under premise 3's
tolerance (the rule of gsc-rules-v1). A disagreement is data, not a failure: counterexample 22 is exactly a page
missing from the detail that the filter request still counts.
"""

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import timedelta

from ggwork_pick.observe.gsc.pageset import PageSet, PageSources, page_set, re2_escape
from ggwork_pick.observe.gsc.params import GSC_RULES_V1
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
from ggwork_pick.observe.gsc.query import GscQuery, GscRow
from ggwork_pick.observe.gsc.urls import UrlShape, classify_url

LADDER = (1024, 2048, 4096, 8192, 16384, 32768)
RESOLUTION = 64
CHUNK_MARGIN_PERCENT = 90  # TR-23a chunks at 90% of the longest regex GSC accepted
SEMANTICS_DAYS = 7
VD_DAYS = 14
SHOWN_CHARS = 120
SHOWN_ROWS = 10
EXAMPLES = 2
FILLER_HEAD, FILLER_TAIL = "^(?:", ")$"
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
    def book_id(self) -> str:
        return self.pset.book_ids[0]


@dataclass(frozen=True, slots=True)
class Part:
    """One part of P4: its table, its values, a request without a clear answer (the item is then undecided), a
    refusal that decides against the design's request shape, and a refusal the design has a fallback for."""

    table: Table | None
    backfill: Mapping[str, object]
    broken: Answer | None = None
    refused: str | None = None
    fallback: str | None = None


def _page_totals(carry: Carry, yesterday) -> tuple[dict[str, int], ...]:
    detail = (carry.get("c_responses") or {}).get(yesterday)
    from_c = sum_by(detail.rows, lambda row: row.keys[1], lambda row: row.impressions) if detail is not None else {}
    return from_c, {row.keys[0]: row.impressions for row in carry.get("pages_7d", ())}


def _seed(ctx: ProbeContext, carry: Carry) -> Seed | None:
    """The new page with the most impressions whose URL page_set's regex matches as it stands."""
    ranked = (url for totals in _page_totals(carry, ctx.yesterday) for url in sorted(totals, key=lambda url: (-totals[url], url)))
    shape = next((shape for shape in (classify_url(url, site_host=ctx.site_host) for url in ranked) if shape.page_set_shape), None)
    if shape is None:
        return None
    sources = PageSources(host=ctx.site_host, rs_ids={}, legacy=())
    return Seed(shape.url, page_set(canonical_id=shape.new_page.book_id, locale=shape.new_page.locale, sources=sources))


def _agrees(flt: int, det: int) -> bool:
    """Premise 3: |X_flt - X_det| <= max(10% x max(X_flt, X_det), 3), in integers."""
    params = GSC_RULES_V1
    return abs(flt - det) * 100 <= max(params.consistency_percent * max(flt, det), params.consistency_floor * 100)


def _cell_compare(flt: dict, det: dict, title: str, labels: tuple[str, str]) -> tuple[int, int, Table]:
    cells = sorted(flt.keys() | det.keys())
    agree = [cell for cell in cells if _agrees(flt.get(cell, 0), det.get(cell, 0))]
    rows = tuple((*cell, str(flt.get(cell, "无行")), str(det.get(cell, "无行"))) for cell in cells if cell not in agree)[:SHOWN_ROWS]
    return len(cells), len(agree), Table(title, (*labels, "过滤请求", "明细合计"), rows)


def _outcome(answer: Answer) -> str:
    return f"{len(answer.response.rows)} 行" if answer.ok else f"HTTP {answer.error.status}（{answer.error.kind}）"


async def _semantics(ctx: ProbeContext, seed: Seed, carry: Carry) -> Part:
    days = (ctx.yesterday - timedelta(days=SEMANTICS_DAYS - 1), ctx.yesterday)
    cases = (
        ("page_set 的锚定正则", seed.regex),
        ("未锚定的 id", seed.book_id),
        ("^ 加 id", "^" + seed.book_id),
        ("前瞻（RE2 不支持）", "(?=https)" + seed.regex),
    )
    answers = [
        await ask(ctx, GscQuery(*days, ("page",), "all", dimension_filter_groups=filters("page", "includingRegex", expression))) for _, expression in cases
    ]
    exact, partial, anchored, lookahead = answers
    listed = {row.keys[0] for row in carry.get("pages_7d", ())}
    expected = {url for url in listed if seed.pset.contains(url)} or {seed.url}
    returned = {row.keys[0] for row in exact.response.rows} if exact.ok else set()
    backfill = {
        "regex_page_set_exact": exact.ok and expected <= returned and all(seed.pset.contains(url) for url in returned),
        "regex_partial_match": (seed.url in {row.keys[0] for row in partial.response.rows}) if partial.ok else None,
        "regex_anchor_honored": (not anchored.response.rows) if anchored.ok else None,
        "regex_re2_only": False if lookahead.ok else (True if lookahead.error.kind == "bad_request" else None),
    }
    table = Table(
        "includingRegex 的语义（近 7 个 PT 日，[page]/all）",
        ("用例", "表达式", "结果"),
        tuple((name, _short(expression), _outcome(answer)) for (name, expression), answer in zip(cases, answers)),
    )
    refused = "page_set 的锚定正则被拒" if not exact.ok and exact.error.kind == "bad_request" else None
    unclear = (
        *((exact,) if not exact.ok and refused is None else ()),
        *(answer for answer in (partial, anchored) if not answer.ok),
        *((lookahead,) if backfill["regex_re2_only"] is None else ()),
    )
    return Part(table, backfill, next(iter(unclear), None), refused)


async def _length(ctx: ProbeContext) -> tuple[Part, LengthSearch]:
    found = await search_limit(lambda length: _accepts(ctx, length))
    suggested = found.max_ok * CHUNK_MARGIN_PERCENT // 100 if found.max_ok else None
    backfill = {"regex_max_length_ok": found.max_ok, "regex_min_length_rejected": found.min_fail, "regex_chunk_length_suggested": suggested}
    rows = tuple((str(length), {True: "接受", False: "400 拒绝", None: "未得到明确回答"}[outcome]) for length, outcome in found.attempts)
    return Part(Table("includingRegex 长度（梯度加二分）", ("长度", "结果"), rows), backfill), found


async def _vh(ctx: ProbeContext, seed: Seed, carry: Carry) -> Part:
    groups = filters("page", "includingRegex", seed.regex)
    answer = await ask(ctx, GscQuery(ctx.yesterday, ctx.yesterday, ("hour", "country"), "hourly_all", dimension_filter_groups=groups))
    backfill = {"vh_supported": answer.ok, "vh_seed_book_id": seed.book_id, "vh_cells": None, "vh_cells_agree": None}
    if not answer.ok:
        refused = "按页面过滤的 [hour,country]（Vh）被拒" if answer.error.kind == "bad_request" else None
        return Part(None, {**backfill, "vh_supported": False if refused else None}, None if refused else answer, refused)
    detail = (carry.get("c_responses") or {}).get(ctx.yesterday)
    if detail is None or carry.get("c_shape") != "hour,page,country":
        return Part(None, backfill)
    flt = sum_by(answer.response.rows, lambda row: row.keys, lambda row: row.impressions)
    det = sum_by((row for row in detail.rows if seed.pset.contains(row.keys[1])), lambda row: (row.keys[0], row.keys[2]), lambda row: row.impressions)
    cells, agree, table = _cell_compare(flt, det, "Vh 与 C 明细不一致的格（昨天，曝光）", ("PT 小时", "国家"))
    return Part(table, {**backfill, "vh_cells": cells, "vh_cells_agree": agree})


async def _vd(ctx: ProbeContext, seed: Seed) -> Part:
    days, groups = (ctx.yesterday - timedelta(days=VD_DAYS - 1), ctx.yesterday), filters("page", "includingRegex", seed.regex)
    answers = [
        await ask(ctx, GscQuery(*days, dimensions, state, dimension_filter_groups=groups))
        for dimensions, state in ((("date", "country"), "all"), (("date", "country"), "final"), (("date", "page", "country"), "all"))
    ]
    vd_all, vd_final, detail = answers
    supported = {"all": vd_all.ok, "final": vd_final.ok}
    backfill = {"vd_supported": supported, "vd_cells": None, "vd_cells_agree": None}
    refused = "按页面过滤的 [date,country]（Vd，all）被拒" if not vd_all.ok and vd_all.error.kind == "bad_request" else None
    fallback = "按页面过滤的 [date,country] 用 final 被拒：Vd 只能用 all" if not vd_final.ok and vd_final.error.kind == "bad_request" else None
    broken = next((answer for answer in answers if not answer.ok and answer.error.kind != "bad_request"), None)
    if not (vd_all.ok and detail.ok):
        return Part(None, backfill, broken, refused, fallback)
    flt = sum_by(vd_all.response.rows, lambda row: row.keys, lambda row: row.impressions)
    det = sum_by((row for row in detail.response.rows if seed.pset.contains(row.keys[1])), lambda row: (row.keys[0], row.keys[2]), lambda row: row.impressions)
    cells, agree, table = _cell_compare(flt, det, "Vd 与 [date,page,country] 明细不一致的格（近 14 个 PT 日，all，曝光）", ("PT 日", "国家"))
    return Part(table, {**backfill, "vd_cells": cells, "vd_cells_agree": agree}, broken, refused, fallback)


def _p4_text(backfill: Mapping[str, object], found: LengthSearch) -> str:
    match = "按 RE2 部分匹配（未锚定的 id 也命中），正则必须锚定" if backfill["regex_partial_match"] else "按整串匹配（未锚定的 id 不命中）"
    anchor = "^ 生效" if backfill["regex_anchor_honored"] else "^ 不生效"
    re2 = "前瞻被拒，只认 RE2 语法" if backfill["regex_re2_only"] else "前瞻被接受，不像 RE2"
    exact = (
        "page_set 的锚定正则返回的页面与 page_set.contains 相符"
        if backfill["regex_page_set_exact"]
        else "page_set 的锚定正则返回的页面与 page_set.contains 不一致"
    )
    return "；".join((f"includingRegex {match}；{anchor}；{re2}；{exact}；{_length_text(backfill, found)}", *_shape_texts(backfill)))


def _length_text(backfill: Mapping[str, object], found: LengthSearch) -> str:
    if found.max_ok is None:
        return f"连 {found.min_fail} 个字符的正则都被拒"
    bound = f"上限在 {found.max_ok} 与 {found.min_fail} 之间" if found.min_fail else f"试到 {found.max_ok} 仍被接受"
    return f"长度{bound}，TR-23a 分块建议不超过 {backfill['regex_chunk_length_suggested']}"


def _shape_texts(backfill: Mapping[str, object]) -> tuple[str, ...]:
    """What Vh and Vd showed: usable, and how many cells agree with the detail within premise 3's tolerance."""
    shapes = (("Vh", "vh", backfill["vh_supported"]), ("Vd", "vd", backfill["vd_supported"]["all"]))
    texts = ()
    for name, key, usable in shapes:
        cells, agree = backfill[f"{key}_cells"], backfill[f"{key}_cells_agree"]
        if cells is None:
            texts = (*texts, f"{name} 可用（没有可比的明细）") if usable else texts
            continue
        tail = f"，{cells - agree} 格不一致" if agree < cells else ""
        texts = (*texts, f"{name} 可用，与明细逐格比较 {agree}/{cells} 格在容差内{tail}")
    return texts


async def p4_regex(ctx: ProbeContext, carry: Carry) -> tuple[Finding, Carry]:
    seed = _seed(ctx, carry)
    if seed is None:
        return Finding("P4", "未定", "没有可作种子的新剧目页（C 与 P7 里都没有合 page_set 形态的新页）：P4 无从测起"), {}
    semantics = await _semantics(ctx, seed, carry)
    length, found = await _length(ctx)
    vh, vd = await _vh(ctx, seed, carry), await _vd(ctx, seed)
    parts = (semantics, length, vh, vd)
    backfill = {name: value for part in parts for name, value in part.backfill.items()}
    tables = tuple(part.table for part in parts if part.table is not None)
    facts = (("种子页", seed.url), ("种子 book_id", seed.book_id), ("page_set 正则", seed.regex))
    refused = next((part.refused for part in parts if part.refused), None)
    broken = next((part.broken for part in parts if part.broken is not None), None)
    if refused:
        text = f"{refused}：用到它的逐剧核对做不了，相关的剧只出描述性标签"
        return Finding("P4", "不支持", text, facts=facts, tables=tables, backfill=backfill), {}
    if broken is not None or found.aborted:
        text = f"有请求没有得到明确回答（{error_text(broken.error) if broken else '长度搜索中断'}），再跑一次"
        return Finding("P4", "未定", text, facts=facts, tables=tables, backfill=backfill), {}
    verdict, lead = ("退路", f"{vd.fallback}；") if vd.fallback else ("支持", "")
    return Finding("P4", verdict, lead + _p4_text(backfill, found), facts=facts, tables=tables, backfill=backfill), {}
