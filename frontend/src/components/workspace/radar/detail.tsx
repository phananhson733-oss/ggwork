"use client";

import { Minus } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  explain,
  fmt,
  safeLink,
  statuses,
  trendsLink,
} from "@/core/radar/presentation";
import { detailSchema, type RadarRow } from "@/core/radar/schema";
import { historicalSignalTone } from "@/core/radar/semantic-tones";
import { useRadarRead } from "@/core/radar/use-radar-read";

import { RadarCurve } from "./curve";
import { HistoricalWindowBadge, RadarGrowth, TrendBadge } from "./semantic";

export function RadarDetailDialog({
  row,
  items,
  onSelect,
}: {
  row: RadarRow | null;
  items: RadarRow[];
  onSelect: (row: RadarRow | null) => void;
}) {
  const query = useRadarRead(row ? `dramas/${row.id}` : null, detailSchema);
  const d = query.data;
  const index = items.findIndex((r) => r.id === row?.id);
  const matched =
    d?.id === row?.id && d?.snapshot_id === row?.snapshot_id ? d : undefined;
  return (
    <Dialog
      open={row !== null}
      onOpenChange={(open) => {
        if (!open) onSelect(null);
      }}
    >
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-4xl">
        <DialogHeader>
          <DialogTitle>{row?.original_title ?? "剧目证据"}</DialogTitle>
          <DialogDescription>
            US 历史快照 · 原始响应尚未核验 · 实验评分仅供规则参考
          </DialogDescription>
        </DialogHeader>
        <div className="flex gap-2">
          <Button
            variant="outline"
            disabled={index <= 0}
            onClick={() => onSelect(items[index - 1]!)}
          >
            上一部
          </Button>
          <Button
            variant="outline"
            disabled={index < 0 || index >= items.length - 1}
            onClick={() => onSelect(items[index + 1]!)}
          >
            下一部
          </Button>
        </div>
        {query.isFetching ? (
          <p role="status">正在读取证据…</p>
        ) : query.error ? (
          <div role="alert">
            {query.error.message}
            <Button variant="outline" onClick={() => void query.refetch()}>
              重试详情
            </Button>
          </div>
        ) : matched ? (
          <>
            <p>
              <TrendBadge
                tone={historicalSignalTone(statuses[matched.series_status])}
              >
                {statuses[matched.series_status]}
              </TrendBadge>{" "}
              · {matched.geo ?? "市场未知"} · {matched.period_days ?? "—"}{" "}
              个日点
            </p>
            <p>
              <TrendBadge
                tone={historicalSignalTone(explain(matched).label)}
                icon={
                  explain(matched).label === "历史高位平稳" ? Minus : undefined
                }
              >
                {explain(matched).label}
              </TrendBadge>
              ：{explain(matched).reason}
            </p>
            <p className="text-muted-foreground">{explain(matched).advice}</p>
            <RadarCurve points={matched.timeline_data} large />
            <p>
              曲线 {matched.series_start ?? "未知"} —{" "}
              {matched.series_end ?? "未知"}
              {matched.is_older_window ? (
                <>
                  {" "}
                  · <HistoricalWindowBadge />
                </>
              ) : null}
            </p>
            <p>
              末 7 日均值较前 7 日：
              <RadarGrowth pilot={matched.pilot} /> · 实验评分{" "}
              {fmt(matched.pilot.score)} · {matched.pilot.tier ?? "未评分"}
            </p>
            <p>
              热度分 {fmt(matched.pilot.heat_score)} / 动量分{" "}
              {fmt(matched.pilot.momentum_score)} / 平台分{" "}
              {fmt(matched.pilot.platform_score)} / 标签分{" "}
              {fmt(matched.pilot.promo_score)}
            </p>
            <p className="text-muted-foreground text-sm">
              总分由热度 35%、动量 30%、平台 20%、标签 15%
              加权，沿用原规则的活跃点与短时脉冲降权。分项分数已经包含降权，不应再按原始指数直接相加。
            </p>
            <details className="text-muted-foreground text-sm">
              <summary className="text-link cursor-pointer">
                查看降权规则与适用边界
              </summary>
              <p className="mt-2">
                曲线含正值不超过 2 天且峰值大于 0 时，热度分乘 0.4，动量分上限为
                30；含正值 3～5 天时按活跃天数折减。末 3 日均值为 0
                且动量为正时，动量分上限为 25；平均指数低于 5
                且动量为正时，上限为
                40。平台归并和标签来自原包，未核验同名剧；跨批次尺度未校准。
              </p>
            </details>
            <p>
              末两日指数变化 {fmt(matched.pilot.velocity_24h)} · 末段变化加速度{" "}
              {fmt(matched.pilot.acceleration)} · 原规则爬升计数{" "}
              {fmt(matched.pilot.climb_days)}
            </p>
            <p className="text-muted-foreground">
              原包题材标签：{matched.genres?.join(" · ") ?? "未提供"}
              ；原包推荐标签：{matched.promo_tags?.join(" · ") ?? "未提供"}
            </p>
            <p className="text-muted-foreground">
              缓存写入：{matched.cache_written_at ?? "未知"}
              （不等于曲线截止日）· {matched.pilot.rules_version}
            </p>
            {matched.synopsis ? <p>{matched.synopsis}</p> : null}
            <details>
              <summary className="text-link cursor-pointer">
                原包结果与重算对账
              </summary>
              <div className="overflow-x-auto">
                <table className="w-full text-left text-sm">
                  <thead>
                    <tr>
                      {["口径", "分数", "等级", "信号"].map((t) => (
                        <th key={t} className="p-2">
                          {t}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {[
                      ["原包保存", matched.original] as const,
                      ["兼容重算", matched.legacy_replay] as const,
                      ["实验评分", matched.pilot] as const,
                    ].map(([label, s]) => {
                      const score = s as typeof matched.original;
                      return (
                        <tr key={String(label)}>
                          <td className="p-2">{String(label)}</td>
                          <td>{fmt(score?.score)}</td>
                          <td>{score?.tier ?? "—"}</td>
                          <td>{score?.prediction_label ?? "不可计算"}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
              <p>
                差异字段：
                {Object.keys(matched.score_differences).join("、") ||
                  "无可报告差异；未知值不补零"}
              </p>
            </details>
            {matched.record_errors.length ? (
              <p role="note">
                核验问题：
                {matched.record_errors
                  .map((e) => `${e.field}: ${e.code}`)
                  .join("；")}
              </p>
            ) : null}
            <div className="flex flex-wrap gap-4">
              {Object.entries(matched.platform_urls ?? {}).map(
                ([platform, url]) =>
                  safeLink(url) ? (
                    <a
                      key={platform}
                      className="text-link"
                      href={safeLink(url)!}
                      target="_blank"
                      rel="noopener noreferrer"
                    >
                      打开 {platform} 原页面
                    </a>
                  ) : null,
              )}
              {trendsLink(matched) ? (
                <a
                  className="text-link"
                  href={trendsLink(matched)!}
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  去 Google 复查 ↗
                </a>
              ) : null}
            </div>
            <p className="text-muted-foreground text-xs">
              Google
              链接按目录片名生成单词条查询，并非原始采集请求；跨批次指数未经校准，不代表绝对搜索量或预测概率。
            </p>
          </>
        ) : null}
      </DialogContent>
    </Dialog>
  );
}
