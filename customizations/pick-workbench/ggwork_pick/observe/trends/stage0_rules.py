"""The full text of trend-rules-v1 for the granularity stage 0 chooses, and D39's data contract change sheet (plan TR-05,
TR-17, D39; design 4.9).

The design fixes the hourly rules and leaves the text final to the stage 0 report ("规则全文在阶段 0 报告里定稿，定稿前
不写规则代码"). The daily rules follow design 4.9's scheme D: 30 daily points, the trailing partial day dropped, whole
7-day blocks compared so the weekday cycle cancels out. H+D judges on D and adds the hourly count as a description only.
Every threshold is a placeholder, versioned, recalibrated after the shadow run (design 4.9 heading).
"""

from types import MappingProxyType

from ggwork_pick.observe.versions import TREND_RULES_VERSION

GRANULARITIES = ("H", "D", "H+D")

HOURLY_PARAMS = MappingProxyType(
    {
        "基线块非零小时下限（B1–B4 每块）": "3",
        "近期块非零小时下限（B5–B6 合计）": "12",
        "上升比 r 下限": "1.5",
        "相对对照 r ÷ r_c 下限": "1.2",
        "对照序列每块非零小时下限（B1–B6）": "3",
        "emerging：B1–B4 非零小时合计上限": "3",
        "emerging：B6 非零小时下限": "8",
        "emerging：单小时占比上限": "40%",
        "cooling：峰值块非零小时下限": "12",
        "cooling：B6 均值 ÷ 峰值块均值上限": "65%",
        "cooling：B6 均值 ÷ B5 均值上限": "70%",
        "确认：window_end 前移区间": "20–28 小时",
        "重复获取形状一致（秩相关）下限": "0.6",
    }
)

DAILY_PARAMS = MappingProxyType(
    {
        "充分性：W1–W4 非零日合计下限 N": "{n}",
        "基线块非零日下限（W1–W3 每块）": "3",
        "近期块非零日下限（W4）": "4",
        "上升比 r 下限": "1.5",
        "相对对照 r ÷ r_c 下限": "1.2",
        "对照序列每块非零日下限（W1–W4）": "3",
        "emerging：W1–W3 非零日合计上限": "3",
        "emerging：W4 非零日下限": "5",
        "emerging：单日占比上限": "40%",
        "cooling：峰值块非零日下限": "5",
        "cooling：W4 均值 ÷ 峰值块均值上限": "65%",
        "确认：window_end 前移区间": "1 天（20–28 小时）",
        "重复获取形状一致（秩相关）下限": "0.6",
    }
)

_COMMON_TAIL = """- **只用相对量**：判定只用非零点数和块与块的比值，不对自归一化指数设绝对门槛。序列整体乘以任何正数，结果不变。
- **只判 ok 与 ok_zero**：其余 fetch_status 不写值，也不覆盖上一次成功值；ok_zero 记 sparse。
- **优先级**：failed/stale > ambiguous/shared_title/unresolved > insufficient/sparse > unstable > cooling > rising/emerging。
- **判定线**：只看裸剧名那条线；同一请求里的变体线只作身份证据，不参与判定（设计 4.7）。
- **不跨剧排名**。"""

_HOURLY = (
    "### {version}（方案 H：小时级 `now 7-d`）\n\n"
    "1. **窗口**：整个批次共用一个 `window_end` = 批次创建时的整点减 {lag} 小时（阶段 0 按 isPartial 的位置回标，见上文「滞后」），"
    "建批次时持久化；取 [window_end−144h, window_end)，切成 B1–B6 六个 24 小时块。窗口内缺小时或含 partial 点，记 `insufficient_window`。"
    "`latest_block_end` = B6 终点 = window_end（D39）。\n"
    "2. **充分性**：B1–B6 非零小时合计不到 12 记 `sparse`。\n"
    "3. **rising**：B1–B4 每块非零小时 ≥3，B5–B6 非零小时合计 ≥12；r = min(B5 均值, B6 均值) ÷ median(B1..B4 均值) ≥1.5，"
    "去掉最高那一小时后重算仍成立；对照序列用同一公式算出 r_c，要求 r ÷ r_c ≥1.2。对照序列须 B1–B6 每块非零小时 ≥3，"
    "否则 r_c 无定义，记 `control_unavailable`：资料页给未校正结果并标注，智能体默认不用。\n"
    "4. **emerging（从零起量）**：B1–B4 非零小时合计 ≤3；B5–B6 非零小时合计 ≥12 且 B6 非零小时 ≥8；单小时占比 ≤40%，"
    "去掉最高一小时仍成立。默认不进智能体。\n"
    "5. **cooling（冲高回退）**：峰值块在 B1–B5 且该块非零小时 ≥12；B6 均值不到峰值块均值的 65%，且不到 B5 均值的 70%。"
    "只作拦截标签，只拦同一国家的「加码」建议。\n"
    "6. **确认**：相邻两天的集合在各自窗口里都判 rising，且两次观测可比，才记 `confirmed`；只有一天记 `first`。"
    "可比指：两次都是当天新采（不是 carried_over）；`window_end` 前移 20–28 小时；查询表达式、颗粒度、数据来源、规则版本与参数都相同；"
    "身份相同，或在冻结的别名版本里相连。确认记录保存两次的集合与判定行编号。同一会话末尾对 first 命中再取一次，"
    "只作重复获取一致性检查：两次形状的秩相关 <0.6 标 `unstable`，不进智能体；它不算独立样本。\n"
    "{tail}"
)

_DAILY = (
    "### {version}（方案 D：日级 `today 1-m`）\n\n"
    "1. **窗口**：去掉末尾 partial 的那一天。整个批次共用一个 `window_end` = 批次创建时刻 floor 到 UTC 日界{lag_days}"
    "（最近完整日的日末；阶段 0 按日级 isPartial 的位置回标），建批次时持久化；取 [window_end−28d, window_end)，"
    "切成 W1–W4 四个 7 天块（W4 最新）。窗口内缺日或含 partial 点，记 `insufficient_window`。"
    "`latest_block_end` = W4 终点 = window_end（D39）。\n"
    "2. **充分性**：W1–W4 非零日合计不到 N={n} 记 `sparse`（N 的取值见上文「N 的取值」）。\n"
    "3. **rising**：W1–W3 每块非零日 ≥3，W4 非零日 ≥4；r = W4 均值 ÷ median(W1..W3 均值) ≥1.5，去掉最高那一天后重算仍成立；"
    "对照序列用同一公式算出 r_c，要求 r ÷ r_c ≥1.2。对照序列须 W1–W4 每块非零日 ≥3，否则记 `control_unavailable`，处理同方案 H。\n"
    "4. **emerging（从零起量）**：W1–W3 非零日合计 ≤3；W4 非零日 ≥5；单日占比 ≤40%，去掉最高一天仍成立。默认不进智能体。\n"
    "5. **cooling（冲高回退）**：峰值块在 W1–W3 且该块非零日 ≥5；W4 均值不到峰值块均值的 65%。只拦同一国家的「加码」建议。\n"
    "6. **确认**：相邻两天的集合都判 rising，且两次观测可比（同方案 H，`window_end` 前移 1 天），记 `confirmed`；"
    "只有一天记 `first`。重复获取一致性检查同方案 H。\n"
    "{tail}"
)

_HOURLY_DESCRIPTION = (
    "### {version}（方案 H+D：D 判定，H 描述）\n\n"
    "判定全部按下面的方案 D。小时级 `now 7-d` 只给通过的剧补一项描述：最近 144 个完整小时里的「日内实测非零小时」数，"
    "写进判定行的 metrics，不参与判定；两者冲突时以 D 为准。A 档单元数减半（设计 4.5）。\n\n"
)


def trend_rules_text(granularity: str, *, n: int, lag_hours: int, lag_days: int = 0) -> str:
    """trend-rules-v1 in full for `granularity` (H, D or H+D), with N and the lags stage 0 re-read: hours for the hourly
    window, whole days (usually none) behind the UTC day boundary for the daily one."""
    if granularity not in GRANULARITIES:
        raise ValueError(f"granularity is one of {GRANULARITIES}")
    hourly = _HOURLY.format(version=TREND_RULES_VERSION, lag=lag_hours, tail=_COMMON_TAIL)
    days_back = f"，再减 {lag_days} 天" if lag_days > 0 else ""
    daily = _DAILY.format(version=TREND_RULES_VERSION, n=n, lag_days=days_back, tail=_COMMON_TAIL)
    if granularity == "H":
        return hourly
    if granularity == "D":
        return daily
    return _HOURLY_DESCRIPTION.format(version=TREND_RULES_VERSION) + daily


def parameters_table(granularity: str, *, n: int) -> str:
    """The placeholder thresholds of the chosen rules, as a markdown table."""
    params = HOURLY_PARAMS if granularity == "H" else DAILY_PARAMS
    rows = "\n".join(f"| {name} | {value.format(n=n)} |" for name, value in params.items())
    return f"| 参数（占位值，影子运行后回标） | 取值 |\n|---|---|\n{rows}"


_CHANGE_SHEET = """### 数据合同变更单（D39，选 {granularity} 时生效）

合同（TR-33）的字段不变，只有取值口径变：

| 项 | 方案 H（合同现状） | 本次（{granularity}） |
|---|---|---|
| 判定行 `window_kind` | `H` | `D` |
| 块定义 | B1–B6，六个 24 小时块，记在判定行 `metrics.blocks` | W1–W4，四个 7 天块（W4 最新），同样记在 `metrics.blocks`{block_fields} |
| `window_end` | 批次整点减滞后小时 | 批次创建时刻 floor 到 UTC 日界（日级滞后不足一天时不再往回减） |
| `latest_block_end` 的算法 | B6 终点 | W4 终点，即最近完整日的日末（UTC） |
| 联动时效（D13） | GSC W0 与 `latest_block_end` 相隔 ≤48 小时 | 口径不变；日级的 `latest_block_end` 比小时级早最多一天，配对会变少，TR-10 接口不改 |
| 资料页（TR-24） | 按判定行的块定义画块 | 同左：块标签「第 1–4 周」，曲线横轴按日，「截至」显示 `latest_block_end` 的日期 |
{hd_row}
- 任务影响：TR-17 按本变更单实现，不改 TR-10、TR-24 的接口（计划第 8 节）{hd_note}。"""


def contract_change_sheet(granularity: str) -> str | None:
    """D39's change sheet for D or H+D; None for H, which is what the contract already describes."""
    if granularity not in GRANULARITIES:
        raise ValueError(f"granularity is one of {GRANULARITIES}")
    if granularity == "H":
        return None
    both = granularity == "H+D"
    hd_row = "| 判定行 `metrics` 增项 | — | `hourly_nonzero`：最近 144 个完整小时的非零小时数（可空，只作描述） |" if both else ""
    hd_note = "；选 H+D 时 TR-17 +0.5 人日，A 档单元减半，TR-18 的分配随之调整" if both else ""
    fields = "，每块 `{label, start, end, nonzero, mean}`"
    return _CHANGE_SHEET.format(granularity=granularity, hd_row=hd_row, hd_note=hd_note, block_fields=fields)
