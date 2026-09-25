"""The stage 0 report as markdown (plan TR-05, section 8; design 4.9, 4.11; D39).

Each section is a function of the report (stage0_report.Report) returning its lines. The interim report marks what
waits for day 2 with PENDING; nothing here calls an unobserved unit zero (premise 1), and a proxy variable is named,
never quoted.
"""

from collections.abc import Iterable, Mapping
from types import MappingProxyType
from typing import Any

from ggwork_pick.observe.trends import stage0_rules
from ggwork_pick.observe.trends.stage0_checks import DESIGN_LAG_HOURS
from ggwork_pick.observe.trends.stage0_metrics import (
    A_TIER_DRAMAS,
    DAILY_JUDGED_MIN,
    DEFAULT_N,
    HOURS_VISIBLE,
    MIN_POSITIVES_OBSERVED,
    POSITIVE_VISIBLE_RATE,
    RELATED_USABLE_RATE,
    SHAPE_CONSISTENT_RHO,
    GateA,
    Series,
    nonzero_days,
    nonzero_hours,
    visible,
)

GSC_AGE_NOTE = "小时级 now 7-d 看不到那时的需求，日级 today 1-m 能覆盖"
PENDING = "待第二天"
UNOBSERVED = "未观测到"
GROUP_NAMES = MappingProxyType({"positive": "正对照", "negative": "反对照", "regional": "区域对照", "market": "市场序列", "seed": "种子"})
BASIS_NAMES = MappingProxyType({"regional": "按区域对照", "positive+regional": "区域对照不足 4 部，按正对照与区域对照合计", "none": "无从估算"})
GRANULARITY_NAMES = MappingProxyType({"H": "H（小时级 now 7-d）", "D": "D（日级 today 1-m）", "H+D": "H+D（D 判定，H 描述）"})

Report = Any  # stage0_report.Report; kept loose here so the two modules do not import each other


def _counts(mapping: Mapping) -> str:
    return "、".join(f"{key} × {count}" for key, count in mapping.items()) or "无"


def _cell_text(text: object) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def _tentative(report: Report) -> str:
    return f"（暂定，{PENDING}）" if report.interim else ""


# ---- header and summary --------------------------------------------------------------------------------------------


def _header(report: Report) -> list[str]:
    when = report.generated_at.strftime("%Y-%m-%d %H:%M UTC")
    if report.interim:
        missing = "、".join(report.missing_days)
        return [
            f"# Trends 阶段 0 中期报告（{missing}的会话还没跑）",
            "",
            f"生成于 {when}。只有已跑那一天的数据：凡标「{PENDING}」的结论都是暂定，第二天的会话跑完后重出报告才定稿。"
            "各对照只在一天里跑，所以每一天只看到每组的一半。",
        ]
    return ["# Trends 阶段 0 报告（定稿）", "", f"生成于 {when}。两天的会话都已跑完，下面的结论按计划 TR-05 与第 8 节定稿。"]


def _verdict(passed: bool | None, reasons: Iterable[str]) -> str:
    detail = "；".join(reasons)
    if passed is None:
        return f"未定：{detail}" if detail else "未定"
    return "过" if passed else f"不过：{detail}"


def _summary(report: Report) -> list[str]:
    tentative = _tentative(report)
    granularity = GRANULARITY_NAMES.get(report.granularity) or ("两种颗粒度都不过" if report.gate_a_passed is False else "未定（闸门 A 未判定）")
    route = report.route.title if report.route else "未定（闸门未判定）"
    rows = [
        ("闸门 A（小时级）", _verdict(report.gate_h.passed, report.gate_h.reasons)),
        ("闸门 A（日级，N=" + str(report.n) + "）", _verdict(report.gate_d.passed, report.gate_d.reasons)),
        ("闸门 B", _verdict(report.gate_b.passed, report.gate_b.reasons)),
        ("颗粒度", granularity),
        ("日级可见的非零日下限 N", str(report.n)),
        ("滞后（window_end 往回退）", f"小时级 {report.lag_hours} 小时；日级 {report.lag_days} 天"),
        ("路线（第 8 节）", route),
    ]
    table = ["| 项 | 结论 |", "|---|---|", *(f"| {name} | {_cell_text(value)}{tentative} |" for name, value in rows)]
    return ["## 结论一览", "", *table]


# ---- inputs and sessions -------------------------------------------------------------------------------------------


def _inputs(report: Report) -> list[str]:
    controls = report.controls
    counts = "、".join(f"{GROUP_NAMES[group]} {len(controls.group(group))}" for group in GROUP_NAMES)
    provenance = [f"- {key}：{value}" for key, value in controls.provenance.items()]
    return [
        "## 输入",
        "",
        f"对照清单生成于 {controls.built_at or '（未记）'}：{counts}。",
        "",
        *provenance,
        "",
        f"正对照的依据是早先的 GSC 导出（时间见上面的来源），离会话已有几周：{GSC_AGE_NOTE}。"
        "所以小时级的正对照可见率偏低，不单独说明 Trends 看不见这些剧，要和日级一起读。",
    ]


def _proxy_line(session: Mapping) -> str:
    names = [name for name, on in (session.get("proxy_env") or {}).items() if on]
    if not names:
        return "  - 代理变量：都没设置（本机直连）"
    return f"  - 代理变量：{'、'.join(names)} 已设置（只记名字不记值）。这次会话的请求可能经过代理，出口不一定是本机直连，读结论时要算上。"


def _breaker_line(session: Mapping) -> str:
    budget, broken = session.get("budget") or {}, session.get("breaker") or {}
    counts = f"跳闸 {broken.get('trips')}、暂停 {broken.get('pauses')}、限流 {broken.get('rate_limited')}"
    half = "是" if broken.get("half_speed") else "否"
    first = f"首次限流 {budget.get('first_limit_at') or '无'}（之前成功 {budget.get('before_first_limit') or '—'} 次）"
    return f"  - 熔断：{counts}、半速 {half}、熄火 {broken.get('extinguished') or '无'}；{first}；状态码 {session.get('status_codes') or '无'}"


def _session_line(day: int, k: int, session: Mapping, meta: Mapping) -> str:
    budget, uncovered = session.get("budget") or {}, session.get("uncovered") or []
    reasons = _counts({reason: sum(1 for _, r in uncovered if r == reason) for reason in dict.fromkeys(r for _, r in uncovered)})
    when = f"目标日 {session.get('target_date')}，{session.get('started_at')} 至 {session.get('finished_at')}"
    sent = f"请求 {session.get('requests')} 次（任务清单 {meta.get('plan_http')} 次，当日已预留 {budget.get('reserved')}/{budget.get('cap')}）"
    covered = f"覆盖 {session.get('covered')} 个单元，未覆盖 {len(uncovered)} 个（{reasons}）"
    return f"- 第 {day} 天第 {k} 次会话：{when}；{sent}；{covered}；预热 {session.get('warmup')}"


def _session_lines(day: int, meta: Mapping) -> list[str]:
    head = f"- 第 {day} 天：采集器 {meta.get('collector_version')}，出口 {meta.get('egress')}"
    sessions = enumerate(meta.get("sessions") or (), 1)
    return [head, *(line for k, s in sessions for line in (_session_line(day, k, s, meta), _breaker_line(s), _proxy_line(s)))]


def _sessions(report: Report) -> list[str]:
    lines = ["## 会话", "", "本机运行，D20 的出口开关不开，不发探测请求。", ""]
    for day in report.days:
        if not day.ran:
            lines.append(f"- {PENDING}：第 {day.day} 天的会话还没跑")
            continue
        lines += _session_lines(day.day, day.meta or {})
    return lines


# ---- gate A --------------------------------------------------------------------------------------------------------


def _rate(visible_count: int, observed: int) -> str:
    return f"{visible_count}/{observed}（{visible_count / observed:.0%}）" if observed else "0/0"


def _gate_a_row(label: str, gate: GateA) -> str:
    estimate = "—" if gate.estimate_daily is None else f"{gate.estimate_daily}（{BASIS_NAMES[gate.estimate_basis]}）"
    return (
        f"| {label} | {_rate(gate.positive.visible, gate.positive.observed)} | {gate.positive.unobserved} | "
        f"{_rate(gate.regional.visible, gate.regional.observed)} | {estimate} | {_cell_text(_verdict(gate.passed, gate.reasons))} |"
    )


def _gate_a(report: Report) -> list[str]:
    return [
        f"## 闸门 A：能不能看见{_tentative(report)}",
        "",
        f"可见：小时级取最近 144 个完整小时，非零小时 ≥{HOURS_VISIBLE}；日级取最近 30 个完整日，非零日 ≥N（N={report.n}）。"
        f"过线：已观测的正对照里可见 ≥{POSITIVE_VISIBLE_RATE:.0%}，且估算每天非泛词判定 ≥{DAILY_JUDGED_MIN} 条"
        f"（A 档约 {A_TIER_DRAMAS} 部剧 × 区域对照可见率）。正对照观测不到 {MIN_POSITIVES_OBSERVED} 部不判定。"
        f"{UNOBSERVED}的单元不进分母，单列。",
        "",
        "| 颗粒度 | 正对照可见 | 正对照未观测到 | 区域对照可见 | 估算每天非泛词判定 | 结论 |",
        "|---|---|---|---|---|---|",
        _gate_a_row("H", report.gate_h),
        _gate_a_row(f"D（N={report.n}）", report.gate_d),
    ]


def _sensitivity(report: Report) -> list[str]:
    rows = [
        f"| {n} | {_rate(g.positive.visible, g.positive.observed)} | {_rate(g.regional.visible, g.regional.observed)} | "
        f"{'—' if g.estimate_daily is None else g.estimate_daily} | {_cell_text(_verdict(g.passed, g.reasons))} |"
        for n, g in report.sensitivity
    ]
    return [
        f"## N 的取值{_tentative(report)}",
        "",
        f"日级可见的非零日下限。默认 N={DEFAULT_N}，对应小时级的下限：四个 7 天块每块至少 3 个非零日。下表是换 N 之后日级闸门 A 怎么变，定 N 由 G2 拍板。",
        "",
        "| N | 正对照可见 | 区域对照可见 | 估算每天判定 | 日级闸门 A |",
        "|---|---|---|---|---|",
        *rows,
    ]


# ---- per control ---------------------------------------------------------------------------------------------------


def _series_cell(series: Series | None, *, n: int, pending: bool) -> str:
    if series is None:
        return PENDING if pending else f"{UNOBSERVED}（没有结果）"
    if not series.observed:
        return f"{UNOBSERVED}（{series.status}）"
    counted = nonzero_hours(series) if series.granularity == "H" else nonzero_days(series)
    unit = "小时" if series.granularity == "H" else "天"
    return f"{counted.nonzero}/{counted.window} {unit}{'，可见' if visible(series, n=n) else ''}"


def _related_cell(line: Mapping | None, *, pending: bool) -> str:
    """Related queries of a control that asked for them; a unit that failed or never came back is unobserved."""
    if line is None:
        return PENDING if pending else f"{UNOBSERVED}（没有结果）"
    related = line.get("related")
    if related is None:
        return f"{UNOBSERVED}（{line.get('status') or line.get('reason') or 'not_run'}）"
    if related.get("widget_missing"):
        return "没有相关查询控件"
    if related.get("status") == "no_data":
        return "空列表"
    if related.get("status") != "ok":
        return f"{UNOBSERVED}（{related.get('status')}）"
    rising = related.get("rising") or ()
    breakout = sum(item.get("formattedValue") == "Breakout" for item in rising)
    return f"热门 {len(related.get('top') or ())} 条、上升 {len(rising)} 条" + (f"（Breakout {breakout}）" if breakout else "")


def _control_row(report: Report, control, by_unit: Mapping[str, Mapping], by_key: Mapping[str, Series]) -> str:
    day = report.day_of(control.id)
    ran = {d.day for d in report.days if d.ran}
    pending = day not in ran if day is not None else report.interim
    hourly, daily = by_key.get(f"{control.id}-h"), by_key.get(f"{control.id}-d")
    related_line = by_unit.get(f"{control.id}-r" if control.group == "seed" else f"{control.id}-d")
    series = ("—", "—") if control.group == "seed" else tuple(_series_cell(s, n=report.n, pending=pending) for s in (hourly, daily))
    related = _related_cell(related_line, pending=pending) if control.related else "—"
    cells = (control.id, GROUP_NAMES[control.group], control.kind, control.geo, _cell_text(control.term), day or "—", *series, related)
    return "| " + " | ".join(str(cell) for cell in cells) + " |"


def _controls(report: Report) -> list[str]:
    by_unit = {line["unit"]: line for line in report.lines}
    by_key = {series.unit: series for series in report.series}
    rows = [_control_row(report, control, by_unit, by_key) for control in report.controls.controls]
    return [
        "## 逐个对照",
        "",
        f"小时级：最近 144 个完整小时里的非零小时；日级：最近 30 个完整日里的非零日，「可见」按 N={report.n}。"
        "反对照里的泛词可见是预期（泛词会把别的需求算进来）；下架剧可见，说明这个标题词被别的东西占着。",
        "",
        "| 对照 | 组 | 类型 | geo | 词 | 第几天 | 小时级 | 日级 | 相关查询 |",
        "|---|---|---|---|---|---|---|---|---|",
        *rows,
    ]


# ---- gate B --------------------------------------------------------------------------------------------------------


def _gate_b(report: Report) -> list[str]:
    gate = report.gate_b
    rows = []
    for day, stats in gate.per_day.items():
        if not stats.attempts:
            rows.append(f"| {day} | {PENDING} | | | |")
            continue
        rows.append(f"| {day} | {stats.attempts} | {stats.usable} | {stats.rate:.0%} | {stats.seeds_ok} |")
    pending = [f"- {PENDING}：{item}" for item in gate.pending]
    return [
        f"## 闸门 B：直连会话稳不稳{'' if gate.final else _tentative(report)}",
        "",
        f"过线：每次会话要了相关查询的单元里可用 ≥{RELATED_USABLE_RATE:.0%}，至少一个种子拿到列表，有回答的 explore 都带 userType；两次会话都要过。",
        "",
        "| 天 | 要相关查询的单元 | 可用 | 可用率 | 种子拿到列表 |",
        "|---|---|---|---|---|",
        *rows,
        "",
        f"- userType：{_counts(gate.user_types)}；有回答但没带 userType 的 explore：{gate.explores_without_user_type}",
        f"- 没有相关查询控件的回答：{gate.widget_missing}；见到 Breakout：{'是' if gate.breakout_seen else '否'}",
        f"- 结论：{_verdict(gate.passed, gate.reasons)}{'' if gate.final else _tentative(report)}",
        *pending,
    ]


# ---- route, lag, interface -----------------------------------------------------------------------------------------


def _route(report: Report) -> list[str]:
    head = [f"## 路线（计划第 8 节）{_tentative(report)}", ""]
    route = report.route
    if route is None:
        a, b = report.gate_a_passed, report.gate_b.passed
        return [*head, f"未定：闸门 A {_verdict(a, ())}，闸门 B {_verdict(b, ())}。两个闸门都判定之后才选路线。"]
    prefix = f"暂定（{PENDING}）：" if report.interim else ""
    return [
        *head,
        f"{prefix}**{route.title}**",
        "",
        *(f"- Trends 侧：{item}" for item in route.trends),
        f"- GSC 侧：{route.gsc}",
        *(f"- 开关与门槛：{item}" for item in route.switches),
        f"- 人日：{route.person_days}",
    ]


def _distribution(lag) -> str:
    return "、".join(f"{hours} 小时 × {count}" for hours, count in lag.distribution.items())


def _lag(report: Report) -> list[str]:
    lag, daily = report.lag, report.lag_daily
    lines = [f"## 滞后{_tentative(report)}", "", "请求所在整点减去最后一个完整点的终点（isPartial 标出的点不算完整）。", ""]
    if lag.suggested is None:
        lines.append(f"- 小时级：没有观测到的序列，沿用设计 4.9 规则 1 的 {DESIGN_LAG_HOURS} 小时，待回标。")
    else:
        same = "与设计一致" if lag.suggested == DESIGN_LAG_HOURS else f"与设计的 {DESIGN_LAG_HOURS} 小时不同，按实测改"
        lines.append(
            f"- 小时级：{len(lag.samples)} 条序列，分布 {_distribution(lag)}。建议 window_end = 批次整点减 {lag.suggested} 小时"
            f"（取看到的最大值，任何窗口都不越过完整小时），{same}。"
        )
    if daily.suggested is None:
        lines.append("- 日级：没有观测到的序列，按 UTC 日界取窗口，待回标。")
    else:
        lines.append(f"- 日级：{len(daily.samples)} 条序列，分布 {_distribution(daily)}。窗口终点 = UTC 日界往回 {report.lag_days} 天。")
    return lines


def _repeat_rows(report: Report) -> list[str]:
    cells = [
        (r.unit, r.original, r.method, r.minutes_apart, r.shift_points, r.common, r.differing, "—" if r.max_abs_diff is None else r.max_abs_diff)
        for r in report.interface.repeats
    ]
    rows = ["| " + " | ".join(str(cell) for cell in row) + " |" for row in cells]
    if not rows:
        return ["- 重复获取：没有两次都观测到的重复单元"]
    return ["", "| 重复单元 | 原单元 | 方法 | 相隔分钟 | 轴前移点数 | 共同完整点 | 不同的点 | 最大差 |", "|---|---|---|---|---|---|---|---|", *rows]


def _interface(report: Report) -> list[str]:
    s = report.interface
    related = s.related
    per_granularity = "；".join(f"{g}：{_counts(s.points[g])}" for g in ("H", "D"))
    partial = "；".join(f"{g}：{_counts(s.partial_positions[g])}" for g in ("H", "D"))
    statuses = "；".join(f"{g}：{_counts(s.statuses[g])}" for g in ("H", "D"))
    methods = "；".join(f"{m}：{_counts(s.methods[m])}" for m in ("GET", "POST"))
    return [
        "## 接口核实",
        "",
        f"- 每条序列的点数：{per_granularity}",
        f"- isPartial 的位置（从末尾数）：{partial}",
        f"- time 字段的类型：{_counts(s.time_types)}；hasData 有缺的线：{s.has_data_missing}",
        f"- 序列状态：{statuses}",
        f"- explore 的请求方法与单元状态：{methods}",
        "- 空响应的形状：序列里 ok_zero（全零）与 no_data（没有数据）的条数见上面的序列状态；相关查询的空列表与没有控件分开计。"
        "每个 API 回答的原文在 raw/day<N>/，按 index.jsonl 对到单元。",
        f"- 相关查询：要了 {related.asked}，有列表 {related.ok}，空列表 {related.empty}，没有控件 {related.widget_missing}，"
        f"失败 {related.failed}，Breakout {related.breakout}；上升值的写法示例：{'、'.join(related.formatted_examples) or '无'}",
        *_repeat_rows(report),
    ]


# ---- the manual comparison, the rules, what is pending -------------------------------------------------------------


def _manual(report: Report) -> list[str]:
    head = ["## 人工对照（U5）", ""]
    if not report.manual:
        return [
            *head,
            "U5：用户在浏览器导出 3 个词的走势 CSV，待补。拿到后运行 `report --manual <文件>`（可重复），"
            f"这里给出与抓取序列的秩相关 ρ，ρ ≥{SHAPE_CONSISTENT_RHO} 为形状一致。",
        ]
    rows = []
    for row in report.manual:
        found = row.comparison
        rho = "—" if found is None or found.rho is None else f"{found.rho:.2f}"
        verdict = row.problem if found is None else found.verdict
        points = "—" if found is None else found.n
        rows.append(f"| {_cell_text(row.name)} | {_cell_text(row.term or '—')} | {row.geo or '—'} | {points} | {rho} | {_cell_text(verdict)} |")
    offset = report.utc_offset_minutes / 60
    return [
        *head,
        f"浏览器导出的时间按 UTC{offset:+g} 对齐；ρ 是对上的完整点上的秩相关，ρ ≥{SHAPE_CONSISTENT_RHO} 为形状一致。",
        "",
        "| 文件 | 词 | 地区 | 对上的点 | ρ | 判定 |",
        "|---|---|---|---|---|---|",
        *rows,
    ]


def _rules(report: Report) -> list[str]:
    head = [f"## trend-rules-v1 全文{_tentative(report)}", ""]
    if report.granularity is None:
        why = "两种颗粒度都没过闸门 A，规则全文不定稿，按上面的路线处理" if report.gate_a_passed is False else "闸门 A 未判定，规则全文暂不给出"
        return [*head, f"{why}。"]
    note = f"H+D：A 档剧数减半（约 {A_TIER_DRAMAS // 2} 部），估算已按减半重算。" if report.granularity == "H+D" else ""
    text = stage0_rules.trend_rules_text(report.granularity, n=report.n, lag_hours=report.lag_hours, lag_days=report.lag_days)
    sheet = stage0_rules.contract_change_sheet(report.granularity)
    table = stage0_rules.parameters_table(report.granularity, n=report.n)
    return [*head, *([note, ""] if note else []), text, "", table, *(["", sheet] if sheet else [])]


def _pending(report: Report) -> list[str]:
    day_two = (
        (
            *report.gate_b.pending,
            "闸门 A 与 N：另一半对照进来后重算",
            "颗粒度、滞后与 trend-rules-v1 全文定稿",
            "路线（计划第 8 节）",
            "explore 的 POST：第二天的重复获取走 POST",
        )
        if report.interim
        else ()
    )
    items = [*(f"- {PENDING}：{item}" for item in day_two), *(["- 待补：U5 人工对照，浏览器导出的走势 CSV"] if not report.manual else [])]
    return ["## 还欠什么", "", *items] if items else []


SECTIONS = (_header, _summary, _inputs, _sessions, _gate_a, _sensitivity, _controls, _gate_b, _route, _lag, _interface, _manual, _rules, _pending)


def render_report(report: Report) -> str:
    blocks = ["\n".join(section(report)) for section in SECTIONS]
    return "\n\n".join(block for block in blocks if block) + "\n"
