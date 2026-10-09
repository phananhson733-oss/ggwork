import type { RadarRow } from "./schema";

export const fmt = (value: number | null | undefined) =>
  value == null
    ? "—"
    : new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 1 }).format(
        value,
      );
export function growth(p: RadarRow["pilot"]) {
  if (p.growth_state === "from_zero") return "从零基数出现";
  if (p.growth_state === "zero_window") return "全零窗口";
  return p.growth_pct == null
    ? "—"
    : `${p.growth_pct > 0 ? "+" : ""}${fmt(p.growth_pct)}%`;
}
// Wording is a presentation of observed fields, never imported HTML recommendations.
export function explain(r: RadarRow) {
  const points = r.recent_series ?? [];
  if (r.series_status === "invalid_series")
    return {
      label: "曲线待核验",
      reason: "原包曲线无法通过数据检查，相关指标不用于判断。",
      advice: "先核对异常字段和原始资料，再查看趋势。",
    };
  if (!points.length)
    return {
      label: "证据不足",
      reason:
        r.series_status === "fallback_unverified"
          ? "只有默认数值特征，没有可核验的曲线。"
          : "这条记录没有可用的历史曲线。",
      advice: "先补充可核验的曲线，暂不依据默认分数选剧。",
    };
  if (r.series_status === "historical_zero")
    return {
      label: "历史曲线全零",
      reason: `末 ${points.length} 个日点均显示 0；不能据此断言没有搜索需求。`,
      advice: "先核对片名、市场与窗口，再结合其它真实信号判断。",
    };
  const active = points.filter((p) => p.value > 0).length;
  const growthText =
    r.pilot.growth_state === "percent"
      ? `末7日均值较前7日 ${growth(r.pilot)}。`
      : r.pilot.growth_state === "from_zero"
        ? "前7日均值为0，末7日出现数值，不计算增长百分比。"
        : r.pilot.growth_state === "zero_window"
          ? "末7日与前7日的均值均为0，不计算增长百分比；不代表没有搜索需求。"
          : "两周比较结果不可用，保留未知。";
  return {
    label: r.pilot.score == null ? "历史曲线有值" : r.pilot.prediction_label,
    reason: `末 ${points.length} 个日点有 ${active} 个大于0。${growthText}`,
    advice:
      r.pilot.score == null
        ? "补齐比较窗口；已有曲线可查看，暂不套用实验等级。"
        : r.pilot.prediction_label === "未命中增强信号"
          ? "先核对同名剧与平台页，保留观察；未命中不等于不值得选。"
          : "核对曲线日期和对应剧目，再决定是否列入人工候选。",
  };
}

export const statuses = {
  historical_data: "历史曲线有数值",
  historical_zero: "历史曲线全零",
  fallback_unverified: "缺少曲线，疑似默认兜底",
  missing_series: "没有可用曲线",
  invalid_series: "曲线异常",
};
export function safeLink(value: string): string | null {
  try {
    const u = new URL(value);
    return ["https:", "http:"].includes(u.protocol) &&
      !u.username &&
      !u.password
      ? u.href
      : null;
  } catch {
    return null;
  }
}
export function trendsLink(r: RadarRow): string | null {
  if (!r.original_title || !r.geo || !/^[A-Z]{2}$/.test(r.geo)) return null;
  const points = r.recent_series ?? [];
  return (
    "https://trends.google.com/trends/explore?" +
    new URLSearchParams({
      q: r.original_title,
      geo: r.geo,
      date: points.length
        ? `${points[0]!.date} ${points.at(-1)!.date}`
        : "today 1-m",
    }).toString()
  );
}
