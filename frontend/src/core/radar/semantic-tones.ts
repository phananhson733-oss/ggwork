import type { TableResult } from "@/core/pick/trends-table-schema";

export type TrendTone = "neutral" | "success" | "info" | "warning" | "danger";

export function historicalSignalTone(label: string): TrendTone {
  switch (label) {
    case "历史末段突增":
    case "历史增速上扬":
    case "历史持续爬升":
      return "success";
    case "历史高位平稳":
    case "历史曲线有值":
    case "历史曲线有数值":
      return "info";
    case "证据不足":
    case "没有可用曲线":
    case "缺少曲线，疑似默认兜底":
    case "曲线待核验":
    case "曲线异常":
      return "warning";
    default:
      return "neutral";
  }
}

export function changeTone(value: number | null): TrendTone {
  return value === null || value === 0
    ? "neutral"
    : value > 0
      ? "success"
      : "danger";
}

export function collectionTone(
  result: TableResult,
  status: string | null,
): TrendTone {
  if (status === "ok_zero") return "neutral";
  if (
    [
      "rate_limited",
      "blocked_redirect",
      "html_body",
      "forbidden",
      "server_error",
      "timeout",
      "parse_error",
    ].includes(status ?? "")
  )
    return "danger";
  if (result === "data" || result === "pending") return "info";
  if (
    result === "no_data" ||
    [
      "truncated",
      "skipped_breaker",
      "deadline",
      "not_reached",
      "unreadable",
    ].includes(status ?? "")
  )
    return "warning";
  return "neutral";
}
