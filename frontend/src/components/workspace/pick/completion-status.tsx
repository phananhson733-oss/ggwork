import { checkedMessageMetadataSchema } from "@/core/pick/completion-types";

/** The host owns final content; this component projects only its closed public receipt. */
export function CompletionStatus({ metadata }: { metadata: unknown }) {
  const parsed = checkedMessageMetadataSchema.safeParse(metadata);
  if (!parsed.success) return null;
  const checked = parsed.data;
  return (
    <div
      className="my-3 space-y-1 border-t pt-3 text-base leading-6"
      aria-label="最终回答核对状态"
    >
      <p>
        {checked.status === "confirmed"
          ? "已核对：本次回答依据已确认。"
          : checked.status === "partial"
            ? "部分完成：未确认内容请保留为待核对。"
            : "未完成：本次没有足够的已核对依据。"}
      </p>
      <p className="text-helper">
        核对时间 {checked.checked_at} · 规则 {checked.checker_version}
      </p>
    </div>
  );
}
