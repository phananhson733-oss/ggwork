"use client";

import { Button } from "@/components/ui/button";
import { dataAsOfLine } from "@/core/pick/format";

import { usePickContext } from "./pick-context";

export function PickReferenceNotice({ threadId }: { threadId: string }) {
  const pick = usePickContext();
  if (!pick) return null;
  const bound = pick.plural;
  if (pick?.referenceLoadingThread === threadId)
    return (
      <section
        role="status"
        className="border-line bg-surface mb-3 rounded-lg border p-3 text-sm"
      >
        <p>正在恢复并核对候选引用…</p>
        <Button
          variant="outline"
          className="min-h-11"
          onClick={pick.clearReferences}
        >
          取消多批引用
        </Button>
      </section>
    );
  if (pick?.referenceErrorThread === threadId)
    return (
      <section
        role="alert"
        className="border-line bg-surface mb-3 rounded-lg border p-3 text-sm"
      >
        <p>候选引用无法恢复，请重新选择或取消引用。</p>
        <Button
          variant="outline"
          className="min-h-11"
          onClick={pick.clearReferences}
        >
          取消多批引用
        </Button>
      </section>
    );
  if (bound?.threadId !== threadId) return null;
  return (
    <section
      aria-label="本轮候选引用"
      className="border-line bg-surface mb-3 rounded-lg border p-3 text-sm"
    >
      <div className="flex items-center justify-between gap-3">
        <strong>本轮引用 {bound.value.references.length} 批候选</strong>
        <Button
          variant="outline"
          className="min-h-11"
          onClick={pick.clearReferences}
        >
          取消多批引用
        </Button>
      </div>
      <ul className="mt-2 max-h-48 space-y-2 overflow-y-auto">
        {bound.value.references.map((ref, index) => {
          const result = bound.results.find((row) => row.id === ref.result_id)!;
          return (
            <li key={ref.result_id} className="break-words">
              第{index + 1}批：
              {result.items
                .filter((item) => ref.item_ids.includes(item.item_id))
                .map((item) => item.title)
                .join("、")}
              <p className="text-helper">{dataAsOfLine(result.data_as_of)}</p>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
