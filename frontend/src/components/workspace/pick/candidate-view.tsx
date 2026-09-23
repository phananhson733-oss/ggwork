"use client";

import { Button } from "@/components/ui/button";
import {
  conditionsLine,
  dataAsOfLine,
  evidenceLine,
  postedLine,
} from "@/core/pick/format";
import { pickRunStatusLabel, type PickResult } from "@/core/pick/types";

export function CandidateView({
  result,
  selected,
  onToggle,
  onSave,
  busy,
  readOnly = false,
}: {
  result: PickResult;
  selected: string[];
  onToggle: (id: string) => void;
  onSave: () => void;
  busy: boolean;
  readOnly?: boolean;
}) {
  return (
    <div className="space-y-4">
      <p role="status" className="text-sm">
        {pickRunStatusLabel[result.run_status]}
      </p>
      <div className="flex items-center justify-between gap-3 border-b pb-3">
        <div>
          <h2 className="font-semibold">本次候选</h2>
          <p className="text-muted-foreground text-sm">
            找到 {result.items.length} 部 / 请求 {result.conditions.limit} 部
            {typeof result.matched_total === "number" &&
              ` · 符合条件共 ${result.matched_total} 部`}
          </p>
        </div>
        {!readOnly && (
          <Button
            size="sm"
            disabled={
              busy || selected.length === 0 || result.run_status !== "success"
            }
            onClick={onSave}
          >
            {busy ? "保存中…" : `保存选中（${selected.length}）`}
          </Button>
        )}
      </div>
      <p className="text-muted-foreground text-xs">
        {conditionsLine(result.conditions)}
      </p>
      <p
        className="text-muted-foreground text-xs"
        data-testid="pick-data-as-of"
      >
        数据截至：{dataAsOfLine(result.data_as_of)}
      </p>
      {result.items.length === 0 && (
        <p className="rounded-lg border border-dashed p-6 text-sm">
          没有符合这次条件的剧目，可以放宽条件后重新查询。
        </p>
      )}
      {result.items.map((item, index) => (
        <article key={item.item_id} className="bg-card rounded-xl border p-4">
          <label className="flex cursor-pointer items-start gap-3">
            {!readOnly && (
              <input
                type="checkbox"
                className="mt-1 size-4 shrink-0"
                aria-label={`选择${item.title}`}
                checked={selected.includes(item.item_id)}
                onChange={() => onToggle(item.item_id)}
                disabled={busy}
              />
            )}
            <span className="min-w-0">
              <span className="font-medium">
                {index + 1}. {item.title}
              </span>
              <span className="text-muted-foreground mt-1 block text-xs">
                {item.theater || "剧场未注明"} · {item.language}
              </span>
            </span>
          </label>
          <p className="mt-3 text-sm leading-6">{item.reason}</p>
          {postedLine(item.posted) && (
            <p className="text-muted-foreground mt-1 text-xs">
              {postedLine(item.posted)}
            </p>
          )}
          {item.warnings.map((warning) => (
            <p
              key={warning}
              className="mt-1 text-xs text-amber-700 dark:text-amber-400"
            >
              {warning}
            </p>
          ))}
          <details className="mt-3 text-xs">
            <summary className="text-muted-foreground cursor-pointer">
              查看依据（{item.evidence.length}）
            </summary>
            {item.evidence.length === 0 ? (
              <p className="mt-2">只有剧库收录记录，暂无额外信号。</p>
            ) : (
              <ul className="mt-2 space-y-3">
                {item.evidence.map((evidence) => (
                  <li
                    key={evidence.citation_id}
                    className="border-l-2 pl-3 break-words"
                  >
                    <p>{evidenceLine(evidence)}</p>
                    <p className="text-muted-foreground">
                      {evidence.observed_at ?? "日期未知"} ·{" "}
                      {evidence.source_ref}
                    </p>
                  </li>
                ))}
              </ul>
            )}
          </details>
        </article>
      ))}
      <p className="text-muted-foreground text-xs">
        这是查询时的资料快照。推荐不代表已经发布或同步到外部系统。
      </p>
    </div>
  );
}
