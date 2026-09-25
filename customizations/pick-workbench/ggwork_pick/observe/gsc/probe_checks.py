"""P1, P2, P3, P5 and P6 of the GSC probe: access, A', C, the watermarks and A'' (plan TR-07; design 5.1-5.4).

Each step takes the probe context and what earlier steps carried, and returns its Finding and what it carries on. The
requests are the shapes design 5.2 names, sent once each for the probe; the verdict rules are in each step's text.
"""

from collections.abc import Mapping
from datetime import date, datetime, timedelta
from types import MappingProxyType

from ggwork_pick.observe.gsc.cutoff import PT, utc
from ggwork_pick.observe.gsc.probe import (
    Answer,
    Finding,
    ProbeContext,
    Table,
    ask,
    error_facts,
    error_text,
    failed,
    failure_verdict,
    filters,
    impressions,
    percent,
    sum_by,
    yes_no,
)
from ggwork_pick.observe.gsc.query import GscQuery, GscResponse, GscRow, format_day, parse_hour

C_DIMENSIONS = ("hour", "page", "country")
FALLBACK_DIMENSIONS = ("hour", "page")
SPLIT_COUNTRIES = 3
SNAKE, CAMEL = ("first_incomplete_hour", "first_incomplete_date"), ("firstIncompleteHour", "firstIncompleteDate")
Carry = Mapping[str, object]


def pt_hour_text(moment: datetime) -> str:
    # The PT wall clock with its offset, as GSC writes first_incomplete_hour (2026-09-25T03:00:00-07:00).
    return moment.astimezone(PT).strftime("%Y-%m-%dT%H:%M:%S%:z")


def c_query(day: date, country: str | None = None, dimensions: tuple[str, ...] = C_DIMENSIONS) -> GscQuery:
    groups = filters("country", "equals", country) if country else ()
    return GscQuery(day, day, dimensions, "hourly_all", dimension_filter_groups=groups)


# ---- P1 ------------------------------------------------------------------------------------------------------------------


async def p1_access(ctx: ProbeContext, carry: Carry) -> tuple[Finding, Carry]:
    """One [date]/all query over the last four PT days: success under Restricted is the access check of design 5.1."""
    query = GscQuery(ctx.today - timedelta(days=3), ctx.today, ("date",), "all")
    answer = await ask(ctx, query)
    if not answer.ok:
        return failed("P1", "真实查询", answer.error, access_ok=False), {}
    rows = answer.response.rows
    facts = (
        ("请求", query.label),
        ("行数", str(len(rows))),
        ("曝光合计", str(impressions(rows))),
        ("responseAggregationType", answer.response.aggregation or "无"),
    )
    if rows:
        conclusion = "Restricted 权限下 searchAnalytics.query 成功并返回数据：接入验收通过，服务账号已由属性 Owner 加入（U2）"
    else:
        conclusion = "查询成功但近 4 个 PT 日没有任何行：权限没有问题，属性可能不是站点所在的那个，先核对 PICK_GSC_SITE_URL"
    return Finding("P1", "支持", conclusion, facts=facts, backfill={"access_ok": True}), {"p1": answer.response}


# ---- P3 ------------------------------------------------------------------------------------------------------------------


def _cells(rows, country: str) -> dict[tuple[str, ...], tuple[int, int]]:
    return {row.keys: (row.clicks, row.impressions) for row in rows if row.keys[-1] == country}


def _split_row(country: str, answer: Answer, unsplit: tuple[GscRow, ...]) -> tuple[tuple[str, ...], bool, bool]:
    """A split table row, whether this country's split was full, and whether its rows agree with the unsplit ones."""
    if not answer.ok:
        return (country, "失败", error_text(answer.error), "—", "—", "—", "—", "—"), False, False
    split, whole = _cells(answer.response.rows, country), _cells(unsplit, country)
    same = sum(1 for keys, cell in split.items() if whole.get(keys) == cell)
    differ = sum(1 for keys, cell in split.items() if keys in whole and whole[keys] != cell)
    only_split, only_whole = len(split.keys() - whole.keys()), len(whole.keys() - split.keys())
    row = (country, str(len(split)), yes_no(answer.response.truncated), str(len(whole)), str(same), str(only_split), str(only_whole), str(differ))
    return row, answer.response.truncated, differ == 0 and only_whole == 0


def _top_countries(rows: tuple[GscRow, ...]) -> tuple[str, ...]:
    totals = sum_by(rows, lambda row: row.keys[-1], lambda row: row.impressions)
    return tuple(sorted(totals, key=lambda country: (-totals[country], country)))[:SPLIT_COUNTRIES]


async def _p3_fallback(ctx: ProbeContext, main: Answer) -> tuple[Finding, Carry]:
    """C refused: try the design's fallback, [hour,page] with countries from daily data (design 5.2)."""
    if main.error.kind != "bad_request":
        return failed("P3", "C（[hour,page,country]）", main.error), {}
    fallback = await ask(ctx, c_query(ctx.yesterday, dimensions=FALLBACK_DIMENSIONS))
    facts = (*error_facts(main.error), ("退路请求", fallback.query.label))
    if not fallback.ok:
        return Finding("P3", failure_verdict(fallback.error), f"C 被拒，退路 [hour,page] 也失败（{error_text(fallback.error)}）", facts=facts), {}
    backfill = {"c_shape": "hour,page", "c_rows_yesterday": len(fallback.response.rows), "c_truncated": fallback.response.truncated}
    conclusion = "[hour,page,country] 被拒（400）：退到 [hour,page] + 日级国家（设计 5.2），国家只能按日从 D、E 取"
    carry = {"c_shape": "hour,page", "c_responses": MappingProxyType({ctx.yesterday: fallback.response})}
    return Finding("P3", "退路", conclusion, facts=facts, backfill=backfill), carry


def _p3_verdict(full: bool, split_full: int, rows: int, consistent: bool) -> tuple[str, str]:
    if full and split_full == 0:
        return "支持", f"一天的 C 满额（昨天 {rows} 行）；按国家拆分后每片都不满额：C 要按国家拆分发送，新鲜数据不翻页"
    if full:
        return "退路", f"按国家拆分后仍有 {split_full} 片满额：退到 [hour,page] + 日级国家（设计 5.2）"
    split = "按国家拆分的结果与整片逐行相同" if consistent else "按国家拆分的结果与整片有出入，见下表"
    return "支持", f"一天一片不满额（昨天 {rows} 行）：C 按 PT 日一片发送即可；{split}"


async def p3_detail(ctx: ProbeContext, carry: Carry) -> tuple[Finding, Carry]:
    """C for yesterday and today, then yesterday split for its three biggest countries, compared row by row."""
    main = await ask(ctx, c_query(ctx.yesterday))
    if not main.ok:
        return await _p3_fallback(ctx, main)
    fresh = await ask(ctx, c_query(ctx.today))
    countries = _top_countries(main.response.rows)
    splits = [(country, await ask(ctx, c_query(ctx.yesterday, country))) for country in countries]
    compared = [_split_row(country, answer, main.response.rows) for country, answer in splits]
    responses = {day: answer.response for day, answer in ((ctx.yesterday, main), (ctx.today, fresh)) if answer.ok}
    carry = {"c_shape": "hour,page,country", "c_responses": MappingProxyType(responses)}
    tables = (_c_table(ctx, main, fresh), _split_table(compared))
    broken = next((answer for answer in (fresh, *(answer for _, answer in splits)) if not answer.ok), None)
    if broken is not None:
        return Finding("P3", failure_verdict(broken.error), f"部分请求失败（{error_text(broken.error)}）", tables=tables), carry
    full = main.response.truncated or fresh.response.truncated
    split_full = sum(1 for _, truncated, _ in compared if truncated)
    consistent = all(agrees for _, _, agrees in compared)
    verdict, conclusion = _p3_verdict(full, split_full, len(main.response.rows), consistent)
    backfill = {
        "c_shape": "hour,page,country",
        "c_rows_yesterday": len(main.response.rows),
        "c_rows_today": len(fresh.response.rows),
        "c_truncated": full,
        "c_needs_country_split": full,
        "c_split_truncated": split_full,
        "c_split_consistent": consistent,
    }
    return Finding("P3", verdict, conclusion, facts=_c_facts(main.response), tables=tables, backfill=backfill), carry


def _c_facts(response: GscResponse) -> tuple[tuple[str, str], ...]:
    rows = response.rows
    return (
        ("昨天的页面数", str(len({row.keys[1] for row in rows}))),
        ("昨天的国家数", str(len({row.keys[2] for row in rows}))),
        ("配额消耗", "API 不回报 load 配额用量；以本项的请求数、耗时、字节数（见文末请求明细）与是否出现配额错误近似"),
    )


def _c_table(ctx: ProbeContext, main: Answer, fresh: Answer) -> Table:
    def line(day: date, answer: Answer) -> tuple[str, ...]:
        if not answer.ok:
            return (format_day(day), "失败", error_text(answer.error), "—")
        return (format_day(day), str(len(answer.response.rows)), yes_no(answer.response.truncated), str(impressions(answer.response.rows)))

    return Table("C 的整日切片", ("PT 日", "行数", "满额", "曝光合计"), (line(ctx.yesterday, main), line(ctx.today, fresh)))


def _split_table(compared) -> Table:
    header = ("国家", "拆分后行数", "满额", "整片里该国行数", "逐行相同", "仅拆分有", "仅整片有", "值不同")
    return Table("按国家拆分（昨天，曝光最多的三个国家）", header, tuple(row for row, _, _ in compared))


# ---- P2 ------------------------------------------------------------------------------------------------------------------


def _p2_verdict(a_prime: Answer) -> tuple[str, str]:
    if a_prime.ok and a_prime.response.aggregation == "byPage":
        return "支持", "小时数据接受 byPage（responseAggregationType 为 byPage）：A′ 可用，全站层 24 小时准入按小时比较 A′ 与 C"
    if a_prime.ok:
        found = a_prime.response.aggregation or "未注明"
        return "退路", f"GSC 接受请求但按 {found} 聚合，byPage 被忽略：A′ 不可用，全站层 24 小时准入退到按 PT 日用 A″（设计 5.2）"
    if a_prime.error.kind == "bad_request":
        return "退路", "小时数据加 byPage 被拒（400）：A′ 不可用，全站层 24 小时准入退到按 PT 日用 A″（设计 5.2）"
    return failure_verdict(a_prime.error), f"A′ 请求失败（{error_text(a_prime.error)}）"


def _hourly(rows) -> dict[datetime, int]:
    return sum_by(rows, lambda row: utc(parse_hour(row.keys[0])), lambda row: row.impressions)


def _percent_text(value: float | None) -> str:
    return "—" if value is None else f"{value}%"


NO_GAP = MappingProxyType({"detail_gap_hours": None, "detail_gap_percent_max": None, "detail_gap_percent_total": None})


def _gap_table(a_prime: GscResponse, a: GscResponse | None, carry: Carry) -> tuple[Table | None, Mapping[str, object]]:
    """A' against the C detail hour by hour, for the complete hours both cover (before A''s watermark)."""
    watermark = a_prime.metadata.first_incomplete_hour or (a.metadata.first_incomplete_hour if a is not None else None)
    detail = carry.get("c_responses") or {}
    if watermark is None or not detail:
        return None, NO_GAP
    totals, plain = _hourly(a_prime.rows), _hourly(a.rows) if a is not None else {}
    shown = _hourly(row for response in detail.values() for row in response.rows)
    hours = sorted(hour for hour in totals if hour < utc(watermark) and hour.astimezone(PT).date() in set(detail))
    rows = tuple(
        (
            pt_hour_text(hour),
            str(totals[hour]),
            str(plain.get(hour, "—")),
            str(shown.get(hour, 0)),
            _percent_text(percent(totals[hour] - shown.get(hour, 0), totals[hour])),
        )
        for hour in hours
    )
    whole, seen = sum(totals[hour] for hour in hours), sum(shown.get(hour, 0) for hour in hours)
    gaps = [gap for gap in (percent(totals[hour] - shown.get(hour, 0), totals[hour]) for hour in hours) if gap is not None]
    backfill = {"detail_gap_hours": len(hours), "detail_gap_percent_max": max(gaps, default=None), "detail_gap_percent_total": percent(whole - seen, whole)}
    table = Table("逐小时明细缺口（A′ 减 C 的明细合计，只看水位之前的完整小时）", ("PT 小时", "A′ 曝光", "A 曝光", "C 明细合计", "缺口占比"), rows)
    return table, backfill


async def p2_hourly_by_page(ctx: ProbeContext, carry: Carry) -> tuple[Finding, Carry]:
    """A and A' over yesterday and today; A' is supported when GSC answers with byPage aggregation."""
    days = (ctx.yesterday, ctx.today)
    a = await ask(ctx, GscQuery(*days, ("hour",), "hourly_all"))
    a_prime = await ask(ctx, GscQuery(*days, ("hour",), "hourly_all", aggregation_type="byPage"))
    verdict, conclusion = _p2_verdict(a_prime)
    supported = {"支持": True, "退路": False, "不支持": False}.get(verdict)  # None: undecided
    table, gap = _gap_table(a_prime.response, a.response, carry) if supported else (None, NO_GAP)
    facts = (
        ("A（默认聚合）", f"{len(a.response.rows)} 行，曝光 {impressions(a.response.rows)}，{a.response.aggregation}" if a.ok else error_text(a.error)),
        ("A′（byPage）", f"{len(a_prime.response.rows)} 行，曝光 {impressions(a_prime.response.rows)}" if a_prime.ok else error_text(a_prime.error)),
    )
    backfill = {
        "a_prime_supported": supported,
        "a_prime_aggregation": a_prime.response.aggregation if a_prime.ok else None,
        **gap,
    }
    return Finding("P2", verdict, conclusion, facts=facts, tables=(table,) if table else (), backfill=backfill), {}


# ---- P5 ------------------------------------------------------------------------------------------------------------------


def _spelling(responses) -> str | None:
    keys = {key for response in responses for key in response.metadata.raw}
    snake, camel = bool(keys & set(SNAKE)), bool(keys & set(CAMEL))
    return {(True, False): "snake_case", (False, True): "camelCase", (True, True): "mixed"}.get((snake, camel))


def _response_row(label: str, day: str, response: GscResponse, now: datetime) -> tuple[str, ...]:
    metadata = response.metadata
    hour, stated = metadata.first_incomplete_hour, metadata.first_incomplete_date
    lag = str(round((now - hour).total_seconds() / 3600, 1)) if hour is not None else "—"
    names = "、".join(sorted(metadata.raw)) or "无"
    return (label, day, pt_hour_text(hour) if hour else "无", format_day(stated) if stated else "无", names, lag)


def _answer_row(label: str, day: str, answer: Answer, now: datetime) -> tuple[str, ...]:
    if answer.ok:
        return _response_row(label, day, answer.response, now)
    return (label, day, "失败", error_text(answer.error), "—", "—")


async def p5_watermarks(ctx: ProbeContext, carry: Carry) -> tuple[Finding, Carry]:
    """A for each of the last three PT days, C for the day before yesterday, daily final; with P1's and P3's answers."""
    days = (ctx.today, ctx.yesterday, ctx.yesterday - timedelta(days=1))
    hourly = [(day, await ask(ctx, GscQuery(day, day, ("hour",), "hourly_all"))) for day in days]
    detail = dict(carry.get("c_responses") or {})
    shape = tuple(str(carry.get("c_shape", ",".join(C_DIMENSIONS))).split(","))
    older = await ask(ctx, c_query(days[2], dimensions=shape))
    final = await ask(ctx, GscQuery(ctx.today - timedelta(days=5), ctx.today, ("date",), "final"))
    now, c_label = ctx.started or ctx.clock.now(), f"C [{','.join(shape)}]"
    p1 = (_response_row("[date]/all（P1）", "近 4 天", carry["p1"], now),) if "p1" in carry else ()
    rows = (
        *(_answer_row("A [hour]", format_day(day), answer, now) for day, answer in hourly),
        *(_response_row(c_label, format_day(day), response, now) for day, response in sorted(detail.items())),
        _answer_row(c_label, format_day(days[2]), older, now),
        *p1,
        _answer_row("[date]/final", "近 6 天", final, now),
    )
    table = Table("各请求的 metadata", ("请求", "PT 日", "first_incomplete_hour", "first_incomplete_date", "字段名", "滞后（小时）"), rows)
    answered = (*(answer.response for _, answer in hourly if answer.ok), *detail.values(), *(item.response for item in (older, final) if item.ok))
    base = _p5_base(carry, answered, final)
    return _p5_finding(ctx, hourly[0][1], detail, base, table), {}


def _p5_base(carry: Carry, answered, final: Answer) -> dict[str, object]:
    daily = carry["p1"].metadata.first_incomplete_date if "p1" in carry else None
    final_date = final.response.metadata.first_incomplete_date if final.ok else None
    return {
        "metadata_spelling": _spelling(answered),
        "daily_first_incomplete_date": format_day(daily) if daily else None,
        "final_first_incomplete_date": format_day(final_date) if final_date else None,
    }


SPAN_TEXT = {
    True: "跨水位那天的 C 带该字段",
    False: "跨水位那天的 C 不带该字段：按设计 5.3 记 watermark_absent，那个切片本轮不可用",
    None: "没有取到跨水位那天的 C",
}


def _p5_finding(ctx: ProbeContext, today_a: Answer, detail, base: dict[str, object], table: Table) -> Finding:
    if not today_a.ok:
        return Finding("P5", failure_verdict(today_a.error), f"今天的 A 失败（{error_text(today_a.error)}）", tables=(table,), backfill=base)
    watermark = today_a.response.metadata.first_incomplete_hour
    if watermark is None:
        conclusion = "今天的 A 不带 first_incomplete_hour：按设计 5.3 本轮不出正式 24 小时窗口；水位一直缺，24 小时标签就只能是描述性的"
        backfill = {**base, "watermark": None, "watermark_lag_hours": None, "c_spanning_day_has_watermark": None}
        return Finding("P5", "不支持", conclusion, tables=(table,), backfill=backfill)
    lag = round(((ctx.started or ctx.clock.now()) - watermark).total_seconds() / 3600, 1)
    spanning = detail.get(watermark.astimezone(PT).date())
    has_field = None if spanning is None else spanning.metadata.first_incomplete_hour is not None
    conclusion = (
        f"A 带 first_incomplete_hour（字段名 {base['metadata_spelling']}），水位 {pt_hour_text(watermark)}，"
        f"比取数时刻滞后 {lag} 小时；早于水位的 PT 日不带该字段是合法的；{SPAN_TEXT[has_field]}"
    )
    backfill = {**base, "watermark": pt_hour_text(watermark), "watermark_lag_hours": lag, "c_spanning_day_has_watermark": has_field}
    return Finding("P5", "支持", conclusion, tables=(table,), backfill=backfill)


# ---- P6 ------------------------------------------------------------------------------------------------------------------


def _daily(response: GscResponse) -> dict[str, int]:
    return {row.keys[0]: row.impressions for row in response.rows}


async def p6_daily_totals(ctx: ProbeContext, carry: Carry) -> tuple[Finding, Carry]:
    """A'' with final and with all over the 16 PT days ending yesterday (D27), day by day."""
    start, end = ctx.yesterday - timedelta(days=15), ctx.yesterday
    final = await ask(ctx, GscQuery(start, end, ("date",), "final", aggregation_type="byPage"))
    whole = await ask(ctx, GscQuery(start, end, ("date",), "all", aggregation_type="byPage"))
    broken = next((answer for answer in (final, whole) if not answer.ok), None)
    if broken is not None:
        return failed("P6", "A″（[date]/byPage）", broken.error), {}
    days = tuple(format_day(start + timedelta(days=offset)) for offset in range(16))
    finals, alls = _daily(final.response), _daily(whole.response)
    both = [day for day in days if day in finals and day in alls]
    gaps = {day: percent(abs(finals[day] - alls[day]), alls[day]) for day in both}
    rows = tuple((day, str(alls.get(day, "无行")), str(finals.get(day, "无行")), _percent_text(gaps.get(day))) for day in days)
    missing = [day for day in days if day not in finals]
    latest = max(finals, default=None)
    largest = max((gap for gap in gaps.values() if gap is not None), default=None)
    backfill = {
        "final_latest_day": latest,
        "final_missing_days": missing,
        "max_final_vs_all_percent": largest,
        "a2_aggregation": {"all": whole.response.aggregation, "final": final.response.aggregation},
    }
    aggregation = f"聚合 all 为 {whole.response.aggregation}、final 为 {final.response.aggregation}"
    conclusion = (
        f"final 最近到 {latest or '无'}，窗口里 {len(missing)} 天没有 final 行（{'、'.join(missing) or '无'}）；"
        f"两份都有的 {len(both)} 天里曝光差异最大 {largest if largest is not None else '—'}%；{aggregation}"
    )
    table = Table("A″ 逐日（曝光）", ("PT 日", "all", "final", "差异"), rows)
    return Finding("P6", "支持", conclusion, tables=(table,), backfill=backfill), {}
