"use client";

import { useState } from "react";

import { Button } from "@/components/ui/button";
import {
  explain,
  fmt,
  growth,
  statuses,
  trendsLink,
} from "@/core/radar/presentation";
import { listSchema, statsSchema, type RadarRow } from "@/core/radar/schema";
import { useRadarRead } from "@/core/radar/use-radar-read";

import { RadarCurve } from "./curve";
import { RadarDetailDialog } from "./detail";

const initial = {
  search: "",
  platform: "",
  tier: "",
  series_status: "",
  series_end: "",
  sort: "",
  tab: "all",
};
const control =
  "mt-1 w-full rounded-md border border-input bg-background px-3 py-2 text-sm";

export function HistoricalRadar({ dailyHref }: { dailyHref: string }) {
  const [filters, setFilters] = useState(initial);
  const [offset, setOffset] = useState(0);
  const [selected, setSelected] = useState<RadarRow | null>(null);
  const stats = useRadarRead("stats", statsSchema);
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(filters))
    if (value) params.set(key, value);
  const list = useRadarRead(
    `dramas?${params}&limit=50&offset=${offset}`,
    listSchema,
  );
  const data = list.data;
  function change(key: keyof typeof initial, value: string) {
    setFilters((old) => ({ ...old, [key]: value }));
    setOffset(0);
    setSelected(null);
  }
  const s = stats.data;
  return (
    <section className="min-w-0 space-y-5" aria-label="历史趋势雷达">
      <div className="border-border bg-card rounded-xl border p-5">
        <h2 className="font-semibold">先判断依据，再决定是否进入人工候选。</h2>
        <p className="text-muted-foreground mt-2 text-sm">
          当前展示 US 市场历史快照，不自动刷新
          Google。日期逐剧标注，原始响应尚未核验。
        </p>
        <div className="mt-3 flex flex-wrap gap-2 text-xs">
          {["剧目：原包目录", "Trends：原包历史曲线", "GSC：此快照未包含"].map(
            (t) => (
              <span
                key={t}
                className="border-border text-muted-foreground rounded border px-2 py-1"
              >
                {t}
              </span>
            ),
          )}
        </div>
        <details className="mt-3 text-sm">
          <summary className="text-link cursor-pointer">
            查看数据来源与计算口径
          </summary>
          <div className="text-muted-foreground mt-2 space-y-2">
            <p>
              此目录独立于生产剧库与全球日级采集批次。来源为已封存的 DramaRadar
              数据包，身份、标签和跨平台归并沿用原包，尚未核验同名剧。
            </p>
            <p>
              实验规则 pilot-rules-v1：热度 35%、动量 30%、平台 20%、标签
              15%；沿用原降权与等级阈值。有效连续曲线至少 14
              个日点才评分。跨批次指数未经校准，评分不代表绝对搜索量、预测概率或正式选剧等级。
            </p>
            <p>
              历史全零曲线不等于没有搜索需求；无曲线和异常数据保持未知。缓存写入日期不等于曲线截止日。
            </p>
            <p>
              包内最近一次缓存写入：
              {s?.snapshot.latest_cache_written_at ?? "未知"}
            </p>
            <p className="break-all">
              快照校验标识：{s?.snapshot.snapshot_id ?? "尚未读取"}
            </p>
          </div>
        </details>
      </div>
      {stats.error ? (
        <div role="alert" className="border-border rounded-xl border p-4">
          {stats.error.message}
          <Button variant="outline" onClick={() => void stats.refetch()}>
            重试统计
          </Button>
        </div>
      ) : null}
      <div className="grid grid-cols-2 gap-3 xl:grid-cols-4">
        {[
          ["目录条目", s ? fmt(s.catalog_total) : "—"],
          ["带历史曲线", s ? fmt(s.with_series) : "—"],
          ["无可用曲线 / 待核验", s ? fmt(s.without_series) : "—"],
          ["快照内最新曲线截止日", s?.latest_series_end ?? "未知"],
        ].map(([label, value]) => (
          <div
            key={label}
            className="border-border bg-card rounded-xl border p-5"
          >
            <p className="text-2xl font-semibold tabular-nums">{value}</p>
            <p className="text-muted-foreground mt-1 text-sm">{label}</p>
          </div>
        ))}
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <div role="tablist" aria-label="历史资料视图" className="flex gap-2">
          {[
            ["all", "综合列表"],
            ["signals", "历史趋势信号"],
          ].map(([value, label]) => (
            <Button
              key={value}
              role="tab"
              aria-selected={filters.tab === value}
              variant={filters.tab === value ? "default" : "outline"}
              onClick={() => change("tab", value!)}
            >
              {label}
            </Button>
          ))}
        </div>
        <a href={dailyHref} className="text-link ml-auto text-sm">
          日级采集记录与状态 →
        </a>
      </div>
      <div className="border-border bg-card grid grid-cols-2 gap-3 rounded-xl border p-4 lg:grid-cols-3 xl:grid-cols-6">
        <label className="text-muted-foreground text-xs">
          搜索剧名
          <input
            type="search"
            value={filters.search}
            maxLength={200}
            onChange={(e) => change("search", e.target.value)}
            placeholder="输入片名或关键词"
            className={control}
          />
        </label>
        <Filter
          label="平台"
          value={filters.platform}
          onChange={(v) => change("platform", v)}
          options={[
            ["", "全部平台"],
            ...["ReelShort", "DramaBox", "FlareFlow"].map((t) => [t, t]),
          ]}
        />
        <Filter
          label="实验等级"
          value={filters.tier}
          onChange={(v) => change("tier", v)}
          options={[
            ["", "全部等级"],
            ...["S", "A", "B", "C"].map((t) => [t, t]),
            ["unrated", "未评分"],
          ]}
        />
        <Filter
          label="数据状态"
          value={filters.series_status}
          onChange={(v) => change("series_status", v)}
          options={[["", "全部状态"], ...Object.entries(statuses)]}
        />
        <Filter
          label="曲线截止日"
          value={filters.series_end}
          onChange={(v) => change("series_end", v)}
          options={[
            ["", "全部历史日期"],
            ...(s?.series_end_dates ?? []).map((t) => [t, t]),
          ]}
        />
        <Filter
          label="排序"
          value={filters.sort}
          onChange={(v) => change("sort", v)}
          options={[
            ["", "视图默认排序"],
            ["score_desc", "实验评分降序"],
            ["title_asc", "片名升序"],
            ["series_end_desc", "曲线截止日降序"],
          ]}
        />
      </div>
      <div className="border-border bg-card overflow-hidden rounded-xl border">
        <div className="border-border flex flex-wrap items-center justify-between gap-3 border-b p-4">
          <h3 className="font-semibold">
            {filters.tab === "signals" ? "历史趋势信号" : "综合列表"}
          </h3>
          <div className="flex flex-wrap items-center gap-3 text-sm">
            <span role="status" className="text-muted-foreground">
              {data
                ? `${fmt(data.total)} 条匹配 · 本页 ${data.items.length} 条`
                : "正在读取历史记录…"}
            </span>
            <a
              href={`/api/pick/radar/export?${params}`}
              className="border-input text-link rounded-md border px-3 py-2"
            >
              导出当前筛选 CSV
            </a>
          </div>
        </div>
        {list.error ? (
          <div role="alert" className="p-5">
            {list.error.message}
            <Button variant="outline" onClick={() => void list.refetch()}>
              重试列表
            </Button>
          </div>
        ) : !data ? (
          <p className="p-5">正在读取历史记录…</p>
        ) : !data.items.length ? (
          <p className="p-5">没有匹配的历史记录，请调整筛选条件。</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[1080px] text-left text-sm">
              <thead className="border-border text-muted-foreground border-b text-xs">
                <tr>
                  {[
                    "序号",
                    "剧目 / 平台 / 来源标签",
                    "关注依据",
                    "Google Trends · 末 7 个日点",
                    "核对建议",
                    "实验评分",
                    "详情 / 复查",
                  ].map((t) => (
                    <th key={t} className="px-4 py-3 font-medium">
                      {t}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {data.items.map((r, i) => {
                  const reading = explain(r);
                  const tags = [
                    ...new Set([...(r.genres ?? []), ...(r.promo_tags ?? [])]),
                  ];
                  const points = r.recent_series ?? [];
                  const check = trendsLink(r);
                  return (
                    <tr
                      key={r.id}
                      className="border-border border-b align-top last:border-0"
                    >
                      <td className="px-4 py-5 tabular-nums">
                        {offset + i + 1}
                      </td>
                      <td className="min-w-56 px-4 py-5">
                        <p className="font-semibold">
                          {r.original_title ?? "未知片名"}
                        </p>
                        <p className="text-muted-foreground mt-1 text-xs">
                          {r.platforms?.join(" · ") ?? "平台未知"}
                        </p>
                        <div
                          className="mt-2 flex max-w-64 flex-wrap gap-1"
                          title={`原包标签：${tags.join(" · ")}`}
                        >
                          {tags.slice(0, 3).map((tag) => (
                            <span
                              key={tag}
                              className="bg-muted text-muted-foreground rounded px-1.5 py-0.5 text-xs"
                            >
                              {tag}
                            </span>
                          ))}
                          {tags.length > 3 ? (
                            <span className="text-muted-foreground text-xs">
                              +{tags.length - 3}
                            </span>
                          ) : null}
                        </div>
                      </td>
                      <td className="min-w-56 px-4 py-5">
                        <span className="bg-muted rounded px-2 py-1 text-xs">
                          {reading.label}
                        </span>
                        <p className="text-muted-foreground mt-3 text-xs leading-5">
                          {reading.reason}
                        </p>
                        <p className="text-muted-foreground mt-1 text-xs">
                          依据：
                          {points.length
                            ? "原包曲线 · 规则计算"
                            : "原包数据状态，非趋势结论"}
                        </p>
                      </td>
                      <td className="px-4 py-5">
                        <RadarCurve points={points} />
                        {points.length ? (
                          <>
                            <p className="text-muted-foreground mt-1 text-xs whitespace-nowrap">
                              {points[0]!.date} — {points.at(-1)!.date}
                            </p>
                            <p className="mt-1 text-xs">
                              末日指数 {fmt(points.at(-1)!.value)} · 非零{" "}
                              {points.filter((p) => p.value > 0).length}/
                              {points.length} 个日点
                            </p>
                            <p className="mt-1 text-xs">
                              末 7 日均值较前 7 日：{growth(r.pilot)}
                            </p>
                          </>
                        ) : (
                          <p className="text-muted-foreground mt-2 text-xs">
                            {statuses[r.series_status]}
                          </p>
                        )}
                        {r.is_older_window ? (
                          <p className="text-warning mt-1 text-xs">
                            较早历史窗口
                          </p>
                        ) : null}
                      </td>
                      <td className="text-muted-foreground min-w-44 px-4 py-5 text-xs leading-6">
                        {reading.advice}
                      </td>
                      <td className="px-4 py-5">
                        <p className="font-semibold whitespace-nowrap tabular-nums">
                          {fmt(r.pilot.score)} · {r.pilot.tier ?? "未评分"}
                        </p>
                        <p className="text-muted-foreground mt-1 text-xs">
                          规则参考
                        </p>
                      </td>
                      <td className="px-4 py-5">
                        <Button
                          variant="outline"
                          size="sm"
                          onClick={() => setSelected(r)}
                        >
                          查看依据
                        </Button>
                        {check ? (
                          <a
                            href={check}
                            target="_blank"
                            rel="noopener noreferrer"
                            title="按目录片名生成单词条查询，非原始采集请求"
                            className="text-link mt-3 block text-xs whitespace-nowrap"
                          >
                            去 Google 复查 ↗
                          </a>
                        ) : null}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
        <div className="flex items-center justify-between gap-2 p-4">
          <Button
            variant="outline"
            disabled={!data || offset === 0}
            onClick={() => {
              setOffset(Math.max(0, offset - 50));
              setSelected(null);
            }}
          >
            上一页
          </Button>
          <span className="text-muted-foreground text-sm">
            {data?.total
              ? `${Math.floor(offset / 50) + 1} / ${Math.ceil(data.total / 50)} 页`
              : "0 / 0 页"}
          </span>
          <Button
            variant="outline"
            disabled={!data || offset + 50 >= data.total}
            onClick={() => {
              setOffset(offset + 50);
              setSelected(null);
            }}
          >
            下一页
          </Button>
        </div>
      </div>
      <RadarDetailDialog
        row={selected}
        items={data?.items ?? []}
        onSelect={setSelected}
      />
    </section>
  );
}
function Filter({
  label,
  value,
  onChange,
  options,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  options: string[][];
}) {
  return (
    <label className="text-muted-foreground text-xs">
      {label}
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className={control}
      >
        {options.map(([v, t]) => (
          <option key={v} value={v}>
            {t}
          </option>
        ))}
      </select>
    </label>
  );
}
