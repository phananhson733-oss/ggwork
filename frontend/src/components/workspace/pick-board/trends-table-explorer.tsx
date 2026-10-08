"use client";

import { useState } from "react";

import { Button } from "@/components/ui/button";
import { trendsCsv } from "@/core/pick/trends-table-export";

import { TABLE, TH } from "./views/trends-table-parts";
import { TrendsTableRowView, type TableLine } from "./views/trends-table-row";

const CONTROL =
  "border-line bg-surface text-ink-1 h-9 rounded-md border px-2 text-[13px]";
const COLUMNS = [
  "剧目 / 来源",
  "关注依据",
  "Google Trends · 近 30 天",
  "变化 / 标签",
  "采集状态",
  "核对 / 操作",
];

export function TrendsTableExplorer({
  lines,
  last,
  date,
}: {
  lines: TableLine[];
  last: string;
  date: string;
}) {
  const [search, setSearch] = useState("");
  const [platform, setPlatform] = useState("");
  const [status, setStatus] = useState("");
  const platforms = [
    ...new Set(lines.map((l) => l.row.platform).filter(Boolean)),
  ].sort();
  const query = search.trim().toLocaleLowerCase();
  const filtered = lines.filter(
    ({ row }) =>
      (!platform || row.platform === platform) &&
      (!status || row.result === status) &&
      (!query ||
        `${row.title} ${row.term}`.toLocaleLowerCase().includes(query)),
  );
  function download() {
    const url = URL.createObjectURL(
      new Blob([trendsCsv(filtered, date, last)], {
        type: "text/csv;charset=utf-8",
      }),
    );
    const a = document.createElement("a");
    a.href = url;
    a.download = `google-trends-${date}.csv`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  return (
    <>
      <div
        className="mb-3 flex flex-wrap items-end gap-2"
        data-trends-controls="true"
      >
        <label className="text-helper flex min-w-40 flex-1 flex-col gap-1 text-xs">
          搜索剧目
          <input
            type="search"
            aria-label="搜索剧目"
            maxLength={200}
            placeholder="剧名或查询词"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className={CONTROL}
          />
        </label>
        <label className="text-helper flex flex-col gap-1 text-xs">
          平台
          <select
            value={platform}
            onChange={(e) => setPlatform(e.target.value)}
            className={CONTROL}
          >
            <option value="">全部平台</option>
            {platforms.map((p) => (
              <option key={p} value={p}>
                {p}
              </option>
            ))}
          </select>
        </label>
        <label className="text-helper flex flex-col gap-1 text-xs">
          采集状态
          <select
            value={status}
            onChange={(e) => setStatus(e.target.value)}
            className={CONTROL}
          >
            <option value="">全部状态</option>
            <option value="data">有数据（含零值）</option>
            <option value="no_data">Google 未返回数据</option>
            <option value="not_fetched">这晚未查到</option>
            <option value="pending">还在查</option>
          </select>
        </label>
        <Button
          variant="outline"
          size="sm"
          onClick={download}
          disabled={!filtered.length}
        >
          导出当前筛选 CSV
        </Button>
      </div>
      <p className="text-helper mb-2 text-xs" role="status">
        当前 {filtered.length} / {lines.length} 部 · 仅筛选本批次，不触发采集
      </p>
      <div className="overflow-x-auto">
        <table className={`${TABLE} min-w-[850px]`}>
          <thead>
            <tr>
              {COLUMNS.map((name) => (
                <th key={name} scope="col" className={TH}>
                  {name}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {filtered.map((line) => (
              <TrendsTableRowView
                key={line.row.unit}
                line={line}
                lastComplete={last}
              />
            ))}
            {!filtered.length ? (
              <tr>
                <td colSpan={6} className="text-helper px-3 py-8 text-center">
                  本批次没有匹配的剧目，请调整筛选。
                </td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </div>
    </>
  );
}
