"""Which judgment rows the agent may use as evidence (plan TR-10, D31; design 4.7, 5.8, section 9 #4; contract section 14).

agent_eligibility returns every exclusion reason that applies, in EXCLUSION_REASONS order; a row is eligible when there is
none. It is asked only about Trends rows in state rising or emerging and about GSC rows: whether the state, geo or country
matches the conditions is the filter's business (TR-27), and set_batch_mismatch comes from cross-batch mapping (TR-26).

- Trends: confirmed, or first with trend_include_first; emerging only when trend_state asks for emerging; the title clear;
  not shared_title; the correspondence confirmed by a person, or strong evidence with trend_include_presumed; neither
  stale nor carried over; tier A; not unstable; the control series available.
- GSC: a formal two-layer admission, and neither migration_suspect nor mapping_changed. No correspondence is asked: the
  page's URL decides which drama it belongs to. A label short of its formal bar leaves the row at present (contract
  section 6), so admission is the only label check.
"""

from dataclasses import dataclass
from types import MappingProxyType

from ggwork_pick.observe.contract import EXCLUSION_REASONS, TREND_STATES, ExclusionReason, ObsConditionFields
from ggwork_pick.observe.contract_rows import StateRow

# The data page's and the agent's text for each reason, in EXCLUSION_REASONS order.
EXCLUSION_TEXT = MappingProxyType(
    {
        "trend_first_only": "只观察到一天的上升（首次），没有要求接受首次",
        "emerging_not_requested": "从零起量观察，没有要求 emerging",
        "title_ambiguous": "剧名有歧义或未判明",
        "shared_title": "同语种同名多行（shared_title）",
        "correspondence_unconfirmed": "对应关系未确认，身份证据为中或弱",
        "correspondence_presumed": "推定对应（强证据、未人工确认），没有要求接受推定对应",
        "stale": "沿用超过 3 天，已陈旧",
        "carried_over": "本批没有刷新到，沿用上次状态",
        "b_tier": "B 档只做筛查",
        "unstable": "同一会话重复获取的形状不一致（unstable）",
        "control_unavailable": "对照序列不可用，只有未校正结果",
        "gsc_descriptive_only": "没过两层准入，只有描述性标签",
        "migration_suspect": "旧页占比在窗口间变化过大，疑似页面迁移",
        "mapping_changed": "旧页解析版本变了，映射带来的差额单列",
        "set_batch_mismatch": "观测集合与剧库批次对不上，按冻结别名也映射不到",
    }
)


@dataclass(frozen=True, slots=True)
class Eligibility:
    reasons: tuple[ExclusionReason, ...]

    @property
    def eligible(self) -> bool:
        return not self.reasons


def _correspondence(row: StateRow, conditions: ObsConditionFields) -> tuple[bool, bool]:
    """(unconfirmed and not presumable, presumed and not asked for)."""
    unconfirmed = row.correspondence != "confirmed"
    strong = row.id_evidence == "strong"
    return unconfirmed and not strong, unconfirmed and strong and not conditions.trend_include_presumed


def _trends_reasons(row: StateRow, conditions: ObsConditionFields) -> set[str]:
    if row.state not in TREND_STATES:
        raise ValueError(f"只对 rising、emerging 的 Trends 行提问，这一行是 {row.state}")
    unconfirmed, presumed = _correspondence(row, conditions)
    checks = {
        "trend_first_only": row.confirmation == "first" and not conditions.trend_include_first,
        "emerging_not_requested": row.state == "emerging" and conditions.trend_state != "emerging",
        "title_ambiguous": row.ambiguity != "clear",
        "shared_title": "shared_title" in row.flags,
        "correspondence_unconfirmed": unconfirmed,
        "correspondence_presumed": presumed,
        "stale": row.stale,
        "carried_over": row.carried_over,
        "b_tier": row.tier == "B",
        "unstable": "unstable" in row.flags,
        "control_unavailable": "control_unavailable" in row.flags,
    }
    return {reason for reason, applies in checks.items() if applies}


def _gsc_reasons(row: StateRow) -> set[str]:
    checks = {
        "gsc_descriptive_only": row.admission != "formal",
        "migration_suspect": "migration_suspect" in row.flags,
        "mapping_changed": "mapping_changed" in row.flags,
    }
    return {reason for reason, applies in checks.items() if applies}


def agent_eligibility(row: StateRow, conditions: ObsConditionFields) -> Eligibility:
    """Every reason `row` may not back an answer under `conditions`, in contract order; no reason means eligible."""
    reasons = _trends_reasons(row, conditions) if row.channel == "trends" else _gsc_reasons(row)
    return Eligibility(tuple(reason for reason in EXCLUSION_REASONS if reason in reasons))
