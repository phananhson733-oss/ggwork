"""The radar's shared wording: premise 1, premise 2 and the design's fixed phrases (plan TR-10, section 2; design 4.7,
5.8, 6.1, 7.3; D11).

Premise 1: a missing row is "not observed", never zero. The unobserved phrases themselves are the contract's (TR-33,
contract section 16 item 18) and are only re-exported here; this module owns the forbidden list. FORBIDDEN_TERMS is the
plan's list word for word; _FORBIDDEN_PATTERNS is what forbidden_in() checks, and it also catches the obvious variants
("零次曝光", "曝光为 0") while leaving a window named W0, a threshold such as "≥20 次曝光" and nonzero-hour counts alone.

Premise 2: two lower bounds agreeing is an admission rule. Admission text says exactly ADMISSION_AGREED and never claims
completeness, verification or independence.
"""

import re
from types import MappingProxyType

from ggwork_pick.observe.contract import LINK_LABEL_TEXT, UNOBSERVED, UNOBSERVED_GSC, UNOBSERVED_TRENDS

__all__ = [
    "ADMISSION_AGREED", "ADMISSION_FORBIDDEN_TERMS", "CAUSE_UNDETERMINED", "CONFIRMATION_TEXT", "DATA_SOURCE_TRENDS",
    "FORBIDDEN_TERMS", "FROM_ZERO_NOTE", "FROM_ZERO_UNCHECKED", "GSC_LABEL_TEXT", "GSC_STATE_TEXT", "IMPRESSIONS_UP",
    "LINK_ACTION_TEXT", "LINK_LABEL_TEXT", "LINK_TRENDS_LOW", "LINK_UNTIMELY", "NOT_JUDGED_AMBIGUOUS", "OBS_NOT_OPEN",
    "OBS_NOT_READY", "OBS_STALE", "PRESUMED_CORRESPONDENCE", "SMALL_BASE_SURGE", "TRENDS_STATE_TEXT", "UNOBSERVED",
    "UNOBSERVED_GSC", "UNOBSERVED_TRENDS", "WITHOUT_RANK_GAIN", "WITH_RANK_GAIN", "admission_forbidden_in", "forbidden_in",
]  # fmt: skip

# ---- premise 1 -----------------------------------------------------------------------------------------------------
FORBIDDEN_TERMS = ("0 曝光", "零曝光", "为零", "没有曝光")
_COUNTED = r"(?:曝光|点击)"
_FORBIDDEN_PATTERNS = (
    re.compile(rf"(?<![0-9A-Za-z.])0\s*(?:次\s*)?{_COUNTED}"),  # 0 曝光, 0 次点击; not W0 曝光 or 20 次曝光
    re.compile(rf"零\s*(?:次\s*)?{_COUNTED}"),  # 零曝光, 零次点击
    re.compile(r"为\s*零"),
    re.compile(rf"{_COUNTED}(?:数|量)?\s*(?:为|是|=|：|:)\s*0(?![0-9.])"),  # 曝光为 0, 点击数为0, 曝光：0 次
    re.compile(rf"没有\s*(?:任何\s*)?{_COUNTED}"),
)


def forbidden_in(text: str) -> tuple[str, ...]:
    """The passages of `text` that call an unobserved count zero (premise 1); empty when the text is clean."""
    return tuple(found.group(0) for pattern in _FORBIDDEN_PATTERNS for found in pattern.finditer(text))


# ---- premise 2 -----------------------------------------------------------------------------------------------------
ADMISSION_AGREED = "两份下界一致（准入）"
ADMISSION_FORBIDDEN_TERMS = ("完整", "已核实", "独立")


def admission_forbidden_in(text: str) -> tuple[str, ...]:
    """The words an admission text must not use (premise 2); "完整" is fine elsewhere, e.g. a complete 24-hour window."""
    return tuple(term for term in ADMISSION_FORBIDDEN_TERMS if term in text)


# ---- GSC (design 5.8) ----------------------------------------------------------------------------------------------
FROM_ZERO_NOTE = "基线未观测到"
GSC_LABEL_TEXT = MappingProxyType(
    {
        "surge": "曝光飙升",
        "from_zero": f"从零起量（{FROM_ZERO_NOTE}）",
        "high_ctr": "高点击率",
        "rank_push": "排名冲顶",
        "rising": "7 天上升",
    }
)
GSC_STATE_TEXT = MappingProxyType({**GSC_LABEL_TEXT, "present": "在该国有两层准入的观测"})
SMALL_BASE_SURGE = "小基数曝光上升"
FROM_ZERO_UNCHECKED = "新出现，基线未核对"
# No causal labels: an impressions rise is described with its rank context and "cause undetermined".
IMPRESSIONS_UP = "曝光上升"
WITH_RANK_GAIN = "伴随平均排名改善 ≥2 位"
WITHOUT_RANK_GAIN = "未伴随排名改善"
CAUSE_UNDETERMINED = "原因未定"

# ---- Trends (design 4.7, 4.9, 4.1) ---------------------------------------------------------------------------------
TRENDS_STATE_TEXT = MappingProxyType({"rising": "上升观察", "emerging": "从零起量观察", "cooling": "冲高回退"})
CONFIRMATION_TEXT = MappingProxyType({"confirmed": "已确认（相邻两天都成立）", "first": "首次（只观察到一天）"})
NOT_JUDGED_AMBIGUOUS = "歧义，不判定（设计如此）"
PRESUMED_CORRESPONDENCE = "推定对应"
DATA_SOURCE_TRENDS = "Data source: Google Trends"

# ---- links (design 6.1) --------------------------------------------------------------------------------------------
# The table's "not observed or low" column: no Trends row, or one judged flat or sparse.
LINK_TRENDS_LOW = f"{UNOBSERVED}或低量"
LINK_UNTIMELY = "时效不符"
# Suggested actions, prompts only: nothing is executed. global_parallel and different_markets suggest nothing.
LINK_ACTION_TEXT = MappingProxyType(
    {
        "both_rising": "进发布清单；生成「可粘贴」行",
        "trends_lead_page": "写博客、补内链、提交索引、进切条候选",
        "trends_lead_distribution": "进 YouTube 等渠道切条候选",
        "site_only": "先核对同名与 URL 迁移；自动进 B 档",
        "cooling": "只拦截该国的「加码」建议",
    }
)

# ---- the agent's answers when it cannot use observations (design 4.10, 7.5; D11) -----------------------------------
OBS_NOT_READY = "趋势数据尚未就绪"
OBS_STALE = "数据陈旧"
OBS_NOT_OPEN = "趋势条件尚未开放"
