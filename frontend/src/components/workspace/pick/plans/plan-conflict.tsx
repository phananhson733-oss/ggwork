"use client";

import { useState } from "react";

import { Button } from "@/components/ui/button";
import {
  type Plan,
  type PlanRowInput,
  type PlanUpdate,
} from "@/core/pick/completion-types";
import {
  editablePlan,
  timezonePreview,
  localTimeChoices,
} from "@/core/pick/plan-draft";

type Draft = ReturnType<typeof editablePlan>;
type Side = "local" | "server";
const fields = {
  account: "发布账号",
  channel: "目标渠道",
  local_time: "当地时间",
  fold: "重复时刻偏移",
  copy_text: "文案草稿",
  note: "个人备注",
} as const;
const foldLabel = (row: PlanRowInput, zone: string) => {
  if (row.fold === null) return "未指定重复时刻";
  const offset = row.local_time
    ? localTimeChoices(row.local_time, zone)[row.fold]?.offset
    : null;
  return `第 ${row.fold + 1} 次${offset ? ` · ${offset}` : "（当前时间无法核对偏移）"}`;
};
const control =
  "min-h-11 w-full rounded-md border border-helper bg-surface p-2 text-base";
type DisplayValue = string | number | boolean | string[] | null | undefined;
const show = (value: DisplayValue): string =>
  value === null || value === undefined || value === ""
    ? "未填"
    : Array.isArray(value)
      ? value.join(" → ")
      : String(value);

/** Compare the complete editable draft; no difference is retained implicitly. */
export function PlanConflictReview({
  base,
  local,
  server,
  onApply,
}: {
  base: Plan;
  local: Draft;
  server: Plan;
  onApply: (draft: Draft, mode: PlanUpdate["timezone_change"]) => void;
}) {
  const remote = editablePlan(server);
  const [choices, setChoices] = useState<Record<string, Side>>({});
  const [clearedFolds, setClearedFolds] = useState<Record<string, boolean>>({});
  const [timeMode, setTimeMode] = useState<PlanUpdate["timezone_change"]>(null);
  const same = (a: unknown, b: unknown) =>
    JSON.stringify(a) === JSON.stringify(b);
  const side = (key: string, a: unknown, b: unknown) =>
    same(a, b) ? "server" : choices[key];
  let complete = true;
  const take = <T,>(key: string, a: T, b: T): T => {
    const chosen = side(key, a, b);
    if (!chosen) complete = false;
    return chosen === "local" ? a : b;
  };
  const title = take("title", local.title, remote.title);
  const timezone = take("timezone", local.timezone, remote.timezone);
  const localIds = local.rows.map((row) => row.row_id);
  const remoteIds = remote.rows.map((row) => row.row_id);
  const preferredOrder = take("order", localIds, remoteIds);
  const ids = [...new Set([...preferredOrder, ...localIds, ...remoteIds])];
  const converted =
    timezone !== server.timezone && timeMode === "keep_instant"
      ? timezonePreview(server, timezone, "keep_instant")
      : null;
  if (timezone !== server.timezone && !timeMode) complete = false;
  const mergedRows: PlanRowInput[] = [];
  for (const id of ids) {
    const a = local.rows.find((row) => row.row_id === id);
    const b = remote.rows.find((row) => row.row_id === id);
    if (!a || !b) {
      if (take(`row:${id}`, !!a, !!b)) {
        const retained = { ...(a ?? b)! };
        const time = b && converted?.find((row) => row.row_id === id);
        if (time)
          Object.assign(retained, {
            local_time: time.local_time,
            fold: time.fold,
          });
        mergedRows.push(retained);
      }
      continue;
    }
    const merged = { ...b };
    for (const key of Object.keys(fields) as (keyof typeof fields)[]) {
      if (converted && (key === "local_time" || key === "fold")) continue;
      Object.assign(merged, { [key]: take(`${id}:${key}`, a[key], b[key]) });
    }
    const time = converted?.find((row) => row.row_id === id);
    if (time)
      Object.assign(merged, { local_time: time.local_time, fold: time.fold });
    mergedRows.push(merged);
  }
  const incompatibleFolds = new Set<string>();
  for (const row of mergedRows) {
    if (clearedFolds[row.row_id]) row.fold = null;
    if (
      row.fold !== null &&
      (!row.local_time ||
        !localTimeChoices(row.local_time, timezone).some(
          (choice) => choice.fold === row.fold,
        ))
    ) {
      incompatibleFolds.add(row.row_id);
      complete = false;
    }
  }
  const compare = (
    key: string,
    label: string,
    a: DisplayValue,
    b: DisplayValue,
    timeConverted = false,
    display?: [string, string],
  ) => (
    <div key={key} className="space-y-1 border-b py-2">
      <p className="font-medium">{label}</p>
      <p className="break-words whitespace-pre-wrap">
        本地：<span>{display?.[0] ?? show(a)}</span>
      </p>
      <p className="break-words whitespace-pre-wrap">
        服务器：<span>{display?.[1] ?? show(b)}</span>
      </p>
      {timeConverted ? (
        <p>按明确选择的服务器时刻转换，结果见下方。</p>
      ) : same(a, b) ? (
        <p className="text-helper">两侧相同</p>
      ) : (
        <label className="block">
          {label}：保留版本
          <select
            className={control}
            value={choices[key] ?? ""}
            onChange={(event) => {
              setClearedFolds({});
              setChoices((current) => ({
                ...current,
                [key]: event.target.value as Side,
              }));
            }}
          >
            <option value="">请明确选择</option>
            <option value="local">本地</option>
            <option value="server">服务器</option>
          </select>
        </label>
      )}
    </div>
  );
  return (
    <section
      className="border-warning-ink space-y-3 border p-4"
      aria-label="计划冲突字段核对"
    >
      <h2 className="text-lg">服务器版本 {server.version} 已变化</h2>
      <p>
        逐项核对本地编辑与最新已保存内容。每个差异都需选择保留版本；此处不会保存到服务器。
      </p>
      {compare("title", "计划名称", local.title, remote.title)}
      {compare("timezone", "计划时区", local.timezone, remote.timezone)}
      {compare("order", "行顺序", localIds, remoteIds)}
      <p className="text-helper">
        选定顺序中没有的新保留行将追加到末尾；行标识与来源不会被合并或替换。
      </p>
      {timezone !== server.timezone && (
        <label className="block">
          时区冲突处理
          <select
            className={control}
            value={timeMode ?? ""}
            onChange={(event) => {
              setClearedFolds({});
              setTimeMode(event.target.value as PlanUpdate["timezone_change"]);
            }}
          >
            <option value="">请明确选择</option>
            <option value="keep_local_time">按所选当地时间使用所选时区</option>
            <option value="keep_instant">
              服务器已有行保留服务器时刻，其他字段按选择
            </option>
          </select>
        </label>
      )}
      {ids.map((id) => {
        const a = local.rows.find((row) => row.row_id === id);
        const b = remote.rows.find((row) => row.row_id === id);
        const source =
          server.rows.find((row) => row.row_id === id) ??
          base.rows.find((row) => row.row_id === id);
        return (
          <fieldset key={id} className="min-w-0 border p-3">
            <legend className="break-all">
              {source?.title ?? id} · {id}
            </legend>
            <p className="break-all">
              来源身份：{(b ?? a)?.identity} · 候选 {(b ?? a)?.source_result_id}{" "}
              / {(b ?? a)?.source_item_id}
            </p>
            {(!a || !b) &&
              compare(
                `row:${id}`,
                `${id} · 行保留状态`,
                a ? "保留此行" : "已移除此行",
                b ? "保留此行" : "已移除此行",
              )}
            {(Object.keys(fields) as (keyof typeof fields)[]).map((key) =>
              a && b ? (
                compare(
                  `${id}:${key}`,
                  `${id} · ${fields[key]}`,
                  a[key],
                  b[key],
                  !!converted && (key === "local_time" || key === "fold"),
                  key === "fold"
                    ? [
                        foldLabel(a, local.timezone),
                        foldLabel(b, remote.timezone),
                      ]
                    : undefined,
                )
              ) : (
                <p key={key} className="break-words whitespace-pre-wrap">
                  {fields[key]}：{show((a ?? b)?.[key])}
                </p>
              ),
            )}
            {converted?.find((row) => row.row_id === id) && (
              <p>
                转换后当地时间：
                {converted.find((row) => row.row_id === id)!.local_time ??
                  "未填"}{" "}
                · 偏移选择：
                {foldLabel(
                  converted.find((row) => row.row_id === id)!,
                  timezone,
                )}
              </p>
            )}
            {incompatibleFolds.has(id) && (
              <div role="alert">
                <p>
                  所选偏移与所选当地时间不一致。请重新选择时间或明确清除偏移。
                </p>
                <Button
                  className="min-h-11"
                  variant="outline"
                  aria-label={`${id} · 清除不适用的偏移`}
                  onClick={() =>
                    setClearedFolds((current) => ({ ...current, [id]: true }))
                  }
                >
                  清除不适用的偏移
                </Button>
              </div>
            )}
            {clearedFolds[id] && (
              <p>已明确清除不适用的偏移；其他字段选择不变。</p>
            )}
          </fieldset>
        );
      })}
      <Button
        className="min-h-11"
        disabled={!complete || !title.trim()}
        onClick={() =>
          onApply(
            { title, timezone, rows: mergedRows },
            timezone === server.timezone ? null : timeMode,
          )
        }
      >
        应用所选字段，以新版本继续
      </Button>
      <Button
        className="min-h-11"
        variant="outline"
        onClick={() => onApply(remote, null)}
      >
        采用服务器版本
      </Button>
    </section>
  );
}
