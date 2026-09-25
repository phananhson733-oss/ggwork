"""The probe's three files: the Markdown report, the findings as JSON, and the raw query answers (plan TR-07).

The report is for people (G2 reads it): one section per item with its verdict first, then the facts, the tables and the
item's request count. The JSON carries the same findings and the values TR-21 and TR-23a take from them (backfill), for
a later task to read without parsing Markdown. The raw file holds what went over the wire for each searchAnalytics.query
(ProbeLog.exchanges): never a header, never the token exchange.
"""

import json
from collections import Counter
from datetime import UTC, date, datetime

from ggwork_pick.observe.gsc.cutoff import pt_date_of
from ggwork_pick.observe.gsc.probe import PROBES, VERDICTS, Finding, ProbeLog, ProbeResult, Table
from ggwork_pick.observe.versions import COLLECTOR_VERSION

# Where each backfilled value goes. A value not listed here is still written, marked "—".
BACKFILL_USE = {
    "access_ok": "设计 5.1 接入验收",
    "a_prime_supported": "TR-21 请求计划：可用则每轮取 A′；否则全站层 24 小时准入按 PT 日用 A″",
    "a_prime_aggregation": "同上：GSC 实际用的聚合",
    "detail_gap_hours": "设计 5.6 第 2 层：参与比较的完整小时数",
    "detail_gap_percent_max": "设计 5.6 的 τ（初值 5%）的参照：逐小时缺口的最大值",
    "detail_gap_percent_total": "同上：合计缺口",
    "detail_gap_skipped_days": "同上：C 满额、不参与缺口计算的 PT 日（截断不算缺口）",
    "c_shape": "TR-21：C 的维度组合",
    "c_rows_yesterday": "TR-21：一天 C 的行数量级",
    "c_rows_today": "同上：当天部分",
    "c_truncated": "TR-21：一天一片是否满额",
    "c_needs_country_split": "TR-21：C 是否要按国家拆分发送",
    "c_split_truncated": "同上：拆分后仍满额的片数",
    "c_split_consistent": "同上：拆分结果与整片是否逐行相同",
    "c_hour_page_rows": "拆分后仍满额时补发的昨天 [hour,page] 的行数（证明设计 5.2 的退路同样放不下）",
    "c_hour_page_truncated": "同上：是否满额",
    "regex_partial_match": "TR-23a：includingRegex 是部分匹配时正则必须锚定（pageset 已锚定）",
    "regex_anchor_honored": "同上：^、$ 是否生效",
    "regex_re2_only": "同上：只能用 RE2 语法（pageset.re2_escape）",
    "regex_page_set_exact": "D25：page_set 的锚定正则与 page_set.contains 取到的页面相同",
    "regex_legacy_exact": "D25、设计 5.5：旧页原串（借来的 ?id= 与百分号编码旧页）按原串精确匹配；否则旧页的逐剧核对不成立",
    "regex_max_length_ok": "TR-23a：实测接受的最长 includingRegex",
    "regex_min_length_rejected": "同上：实测被拒的最短长度",
    "regex_chunk_length_suggested": "TR-23a：PageSet.regex_chunks 的 max_length（实测上限的 90%）",
    "vh_supported": "TR-23a：Vh（[hour,country] + 页面正则）",
    "vh_seed_book_id": "P4 用来比较的种子身份",
    "vh_windows": "TR-23a、前提 3：Vh 与未过滤的 C 明细按国家（含 ALL）比较的窗口数（昨天整个 PT 日）",
    "vh_windows_agree": "其中在前提 3 容差内的窗口数（coverage.consistent，gsc-rules-v1 的参数）",
    "vh_cells": "辅助：Vh 与 C 明细逐（小时，国家）比较的格数",
    "vh_cells_agree": "辅助：其中在前提 3 容差内的格数",
    "vh_detail_truncated": "比较用的 C 是否满额（满额时合计是被截断的下界）",
    "vd_supported": "TR-23a：Vd（[date,country] + 页面正则，all 与 final）",
    "vd_windows": "TR-23a、前提 3：Vd 与未过滤的 [date,page,country] 明细按国家（含 ALL）比较的窗口数",
    "vd_windows_agree": "其中在前提 3 容差内的窗口数（coverage.consistent，gsc-rules-v1 的参数）",
    "vd_cells": "辅助：Vd 与明细逐（PT 日，国家）比较的格数",
    "vd_cells_agree": "辅助：其中在前提 3 容差内的格数",
    "vd_detail_truncated": "比较用的明细是否有一天满额",
    "vd_detail_days": "比较用的明细 PT 日（每天一次未过滤的 [date,page,country]/all）",
    "metadata_spelling": "TR-06 query.py：metadata 的字段名，确认后可只收这一种拼法",
    "watermark": "设计 5.3：本次看到的 A 的水位",
    "watermark_lag_hours": "设计 5.4：完整水位的滞后（一次观察）",
    "c_spanning_day_has_watermark": "设计 5.3：跨水位那天的 C 是否带水位字段",
    "daily_first_incomplete_date": "设计 5.3：[date]/all 的 first_incomplete_date",
    "final_first_incomplete_date": "设计 5.3：[date]/final 的 first_incomplete_date",
    "final_latest_day": "TR-21：E 取 D−3 到 D−5 的依据",
    "final_missing_days": "TR-21、D27：A″f 缺行的日子记「无行」",
    "max_final_vs_all_percent": "D27：final 与 all 的差异",
    "a2_aggregation": "D27：A″ 的聚合",
    "site_host": "D25、pageset.SITE_HOST：正则的主机部分",
    "new_page_hosts": "同上：新剧目页实际出现的主机",
    "new_page_count": "P7 看到的新剧目页条数",
    "new_page_shape_mismatch": "D25：page_set 的锚定正则漏掉的新剧目页条数",
    "slug_percent_encoded": "设计 5.5：原串精确匹配，slug 是否百分号编码",
    "percent_escape_case": "同上：编码的大小写",
    "url_kinds": "设计 5.5：各类页面的条数",
}


def _cell(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def _table(table: Table) -> str:
    lines = (
        f"### {table.title}",
        "",
        f"| {' | '.join(_cell(name) for name in table.header)} |",
        f"|{'---|' * len(table.header)}",
        *(f"| {' | '.join(_cell(value) for value in row)} |" for row in table.rows),
    )
    return "\n".join(lines) if table.rows else f"### {table.title}\n\n（无）"


def _value(value: object) -> str:
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True)


def _stamp(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")


def _requests_of(log: ProbeLog, probe: str) -> str:
    mine = [item.metrics for item in log.metered if item.step == probe]
    elapsed, size = sum(metrics.elapsed_ms for metrics in mine), sum(metrics.bytes for metrics in mine)
    return f"本项请求：{len(mine)} 次，合计 {elapsed:.0f} ms，{size} 字节"


def _section(finding: Finding, log: ProbeLog) -> str:
    parts = [f"## {finding.probe} {PROBES[finding.probe]}", "", f"**结论：{finding.verdict}**", "", finding.conclusion]
    if finding.facts:
        parts = [*parts, "", *(f"- {name}：{_cell(value)}" for name, value in finding.facts)]
    parts = [*parts, *(item for table in finding.tables for item in ("", _table(table)))]
    return "\n".join([*parts, "", _requests_of(log, finding.probe)])


def report_day(result: ProbeResult) -> date:
    """The PT day the run started on: the day GSC's data counts in, and the day the files are named by."""
    return pt_date_of(result.started_at)


def _header(result: ProbeResult, log: ProbeLog, site_url: str, account: str) -> str:
    counts = Counter(finding.verdict for finding in result.findings)
    tally = "、".join(f"{verdict} {counts[verdict]}" for verdict in VERDICTS if counts[verdict])
    tokens = sum(1 for item in log.metered if item.metrics.endpoint == "token")
    quota = sum(1 for item in log.metered if item.metrics.error in ("quota_short", "quota_long"))
    state = "七项都有明确结论" if result.decided else "仍有未定或未测的项，处理后重跑"
    return "\n".join(
        (
            f"# GSC 实测报告（TR-07）{report_day(result).strftime('%Y-%m-%d')}（PT 日）",
            "",
            f"- 属性：{site_url}",
            f"- 服务账号：{account}",
            f"- 运行：{_stamp(result.started_at)} 到 {_stamp(result.finished_at)}",
            f"- 请求：{len(log.metered)} 次（token {tokens} 次），配额错误 {quota} 次",
            f"- 采集合同：{COLLECTOR_VERSION}",
            f"- 结论：{tally}；{state}",
            "- 判读：支持、不支持、退路是明确结论；未定是该给出结论的回答没拿到（配额或临时故障），未测是运行在前面停下了",
        )
    )


def _summary(result: ProbeResult) -> str:
    rows = (f"| {finding.probe} | {_cell(PROBES[finding.probe])} | {finding.verdict} | {_cell(finding.conclusion)} |" for finding in result.findings)
    return "\n".join(("## 汇总", "", "| 项 | 内容 | 结论 | 说明 |", "|---|---|---|---|", *rows))


def _backfill(result: ProbeResult) -> str:
    rows = (f"| {name} | {_cell(_value(value))} | {_cell(BACKFILL_USE.get(name, '—'))} |" for name, value in result.backfill.items())
    return "\n".join(("## 回填（TR-21、TR-23a、TR-06）", "", "| 参数 | 值 | 用在 |", "|---|---|---|", *rows))


def _flag(value: bool | None) -> str:
    return "—" if value is None else ("是" if value else "否")


def _request_rows(log: ProbeLog) -> str:
    def line(number: int, step: str | None, metrics) -> str:
        rows = "—" if metrics.rows is None else metrics.rows
        cells = (
            number,
            step or "—",
            _cell(metrics.label),
            metrics.status or "—",
            rows,
            _flag(metrics.truncated),
            f"{metrics.elapsed_ms:.0f}",
            metrics.bytes,
            metrics.error or "—",
        )
        return f"| {' | '.join(str(cell) for cell in cells)} |"

    rows = (line(number, item.step, item.metrics) for number, item in enumerate(log.metered, start=1))
    return "\n".join(("## 请求明细", "", "| # | 项 | 请求 | HTTP | 行数 | 满额 | 耗时 ms | 字节 | 错误 |", "|---|---|---|---|---|---|---|---|---|", *rows))


def render_markdown(result: ProbeResult, log: ProbeLog, *, site_url: str, account: str) -> str:
    sections = (
        _header(result, log, site_url, account),
        _summary(result),
        *(_section(finding, log) for finding in result.findings),
        _backfill(result),
        _request_rows(log),
    )
    return "\n\n".join(sections) + "\n"


def _finding_json(finding: Finding) -> dict:
    return {
        "probe": finding.probe,
        "title": PROBES[finding.probe],
        "verdict": finding.verdict,
        "conclusion": finding.conclusion,
        "facts": [list(pair) for pair in finding.facts],
        "tables": [{"title": table.title, "header": list(table.header), "rows": [list(row) for row in table.rows]} for table in finding.tables],
        "backfill": dict(finding.backfill),
    }


def findings_json(result: ProbeResult, log: ProbeLog, *, site_url: str, account: str) -> dict:
    return {
        "kind": "gsc-probe",
        "site_url": site_url,
        "service_account": account,
        "collector_version": COLLECTOR_VERSION,
        "started_at": _stamp(result.started_at),
        "finished_at": _stamp(result.finished_at),
        "decided": result.decided,
        "verdicts": {finding.probe: finding.verdict for finding in result.findings},
        "backfill": dict(result.backfill),
        "findings": [_finding_json(finding) for finding in result.findings],
        "requests": [
            {"step": item.step, "endpoint": item.metrics.endpoint, "label": item.metrics.label, "status": item.metrics.status, "rows": item.metrics.rows,
             "truncated": item.metrics.truncated, "elapsed_ms": item.metrics.elapsed_ms, "bytes": item.metrics.bytes, "error": item.metrics.error}
            for item in log.metered
        ],
    }  # fmt: skip


def raw_jsonl(log: ProbeLog) -> bytes:
    return "".join(json.dumps(exchange.as_json(), ensure_ascii=False) + "\n" for exchange in log.exchanges).encode("utf-8")
