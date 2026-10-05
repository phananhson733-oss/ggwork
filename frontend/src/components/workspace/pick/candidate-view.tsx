"use client";

import { Button } from "@/components/ui/button";
import {
  conditionsLine,
  dataAsOfLine,
  evidenceLine,
  postedLine,
} from "@/core/pick/format";
import { itemCheckHref, replayLink } from "@/core/pick/links";
import {
  hotScopeLine,
  itemFactsLine,
  type PickNotesState,
  type PickResultNotes,
  zeroDiagnosisLines,
} from "@/core/pick/notes";
import {
  pickRunStatusLabel,
  type PickItem,
  type PickResult,
} from "@/core/pick/types";

/** Opens one row's evidence page in the pick board, in a new tab so the chat stays. */
export function RowCheckLink({ href, title }: { href: string; title: string }) {
  return (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      aria-label={`在选剧资料核对：${title}`}
      className="text-link text-xs hover:underline"
    >
      在选剧资料核对
    </a>
  );
}

/**
 * 「回放这份候选」on its own line, in a new tab like RowCheckLink. Rendered only
 * while REPLAY_LINK_ENABLED is on (P4-2, critique A4).
 */
export function ReplayLink({ href }: { href: string }) {
  return (
    <p>
      <a
        href={href}
        target="_blank"
        rel="noopener noreferrer"
        className="text-link text-xs hover:underline"
      >
        回放这份候选
      </a>
    </p>
  );
}

function EvidenceDetails({ item }: { item: PickItem }) {
  return (
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
                {evidence.observed_at ?? "日期未知"} · {evidence.source_ref}
              </p>
            </li>
          ))}
        </ul>
      )}
    </details>
  );
}

function CandidateCard({
  item,
  index,
  result,
  selected,
  onToggle,
  busy,
  readOnly,
  facts,
}: {
  item: PickItem;
  index: number;
  result: PickResult;
  selected: string[];
  onToggle: (id: string) => void;
  busy: boolean;
  readOnly: boolean;
  facts: string | null;
}) {
  const posted = postedLine(item.posted, result.conditions);
  const checkHref = itemCheckHref(result.data_as_of, item.identity);
  return (
    <article className="bg-card rounded-lg border p-4">
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
          {facts && (
            <span className="text-muted-foreground mt-1 block text-xs">
              {facts}
            </span>
          )}
        </span>
      </label>
      <p className="mt-3 text-sm leading-6">{item.reason}</p>
      {posted && <p className="text-muted-foreground mt-1 text-xs">{posted}</p>}
      {item.warnings.map((warning) => (
        <p key={warning} className="text-warning-ink mt-1 text-xs">
          {warning}
        </p>
      ))}
      {checkHref && (
        <p className="mt-2">
          <RowCheckLink href={checkHref} title={item.title} />
        </p>
      )}
      <EvidenceDetails item={item} />
    </article>
  );
}

/** What the model was told beside the result: stale-data warnings and what counted as hot. */
function ResultNotices({ notes }: { notes: PickNotesState | undefined }) {
  if (notes?.kind === "gone" || notes?.kind === "error")
    return (
      <p className="text-muted-foreground text-xs" data-testid="pick-notes">
        {notes.kind === "gone" ? notes.message : "依据说明暂不可用，可稍后刷新"}
      </p>
    );
  if (notes?.kind !== "notes") return null;
  const { data_notices: notices = [], hot_scope: hot } = notes.notes;
  if (notices.length === 0 && !hot) return null;
  return (
    <div className="space-y-1 text-xs" data-testid="pick-notes">
      {notices.map((notice) => (
        <p key={notice} className="text-warning-ink">
          {notice}
        </p>
      ))}
      {hot && <p className="text-muted-foreground">{hotScopeLine(hot)}</p>}
    </div>
  );
}

function EmptyResult({ notes }: { notes: PickResultNotes | undefined }) {
  const diagnosis = notes?.zero_diagnosis;
  if (!diagnosis)
    return (
      <p className="rounded-lg border border-dashed p-6 text-sm">
        没有符合这次条件的剧目，可以放宽条件后重新查询。
      </p>
    );
  const { lead, steps, caution } = zeroDiagnosisLines(diagnosis);
  return (
    <div
      className="rounded-lg border border-dashed p-6 text-sm"
      data-testid="pick-zero-diagnosis"
    >
      <p>没有符合这次条件的剧目。{lead}</p>
      {steps.length > 0 && (
        <ul className="mt-2 list-disc space-y-1 pl-5">
          {steps.map((step) => (
            <li key={step}>{step}</li>
          ))}
        </ul>
      )}
      {caution && <p className="text-muted-foreground mt-2">{caution}</p>}
    </div>
  );
}

export function CandidateView({
  result,
  selected,
  onToggle,
  onSave,
  busy,
  readOnly = false,
  notes,
}: {
  result: PickResult;
  selected: string[];
  onToggle: (id: string) => void;
  onSave: () => void;
  busy: boolean;
  readOnly?: boolean;
  /** GET /results/{id}/notes; absent while loading or where nothing reads it. */
  notes?: PickNotesState;
}) {
  const replayHref = replayLink(result);
  const read = notes?.kind === "notes" ? notes.notes : undefined;
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
      <ResultNotices notes={notes} />
      {replayHref && <ReplayLink href={replayHref} />}
      {result.items.length === 0 && <EmptyResult notes={read} />}
      {result.items.map((item, index) => (
        <CandidateCard
          key={item.item_id}
          item={item}
          index={index}
          result={result}
          selected={selected}
          onToggle={onToggle}
          busy={busy}
          readOnly={readOnly}
          facts={itemFactsLine(read?.item_facts[item.item_id])}
        />
      ))}
      <p className="text-muted-foreground text-xs">
        这是查询时的资料快照。推荐不代表已经发布或同步到外部系统。
      </p>
    </div>
  );
}
