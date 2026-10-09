"use client";

import { Button } from "@/components/ui/button";
import { type FeedbackItem } from "@/core/pick/feedback-schema";
import {
  conditionsLine,
  dataAsOfLine,
  evidenceLine,
  evidenceDate,
} from "@/core/pick/format";
import { itemCheckHref, replayLink } from "@/core/pick/links";
import {
  hotScopeLine,
  itemFactsLine,
  itemChecks,
  notesReferenceLine,
  primaryEvidence,
  type PickItemFacts,
  type PickNotesState,
  type PickResultNotes,
  zeroDiagnosisLines,
} from "@/core/pick/notes";
import {
  pickRunStatusLabel,
  type PickItem,
  type PickResult,
} from "@/core/pick/types";

import { FeedbackEvidence, FeedbackSummary } from "./feedback-evidence";

/** Opens one row's evidence page in the pick board, in a new tab so the chat stays. */
export function RowCheckLink({ href, title }: { href: string; title: string }) {
  return (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      aria-label={`在选剧资料核对：${title}（新标签页）`}
      className="text-link inline-flex min-h-11 items-center text-base hover:underline"
    >
      在选剧资料核对（新标签页）
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
        className="text-link inline-flex min-h-11 items-center text-base hover:underline"
      >
        回放这份候选（新标签页）
      </a>
    </p>
  );
}

function EvidenceDetails({ item }: { item: PickItem }) {
  return (
    <details className="mt-3 text-base">
      <summary className="text-muted-foreground min-h-11 cursor-pointer py-3">
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
                {evidenceDate(evidence.observed_at)} · {evidence.source_ref}
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
  feedback,
}: {
  item: PickItem;
  index: number;
  result: PickResult;
  selected: string[];
  onToggle: (id: string) => void;
  busy: boolean;
  readOnly: boolean;
  facts: PickItemFacts | undefined;
  feedback?: FeedbackItem | null;
}) {
  const checks = itemChecks(item, result.conditions, facts);
  const primary = primaryEvidence(item, result.conditions);
  const checkHref = itemCheckHref(result.data_as_of, item.identity);
  return (
    <article className="bg-card min-w-0 rounded-lg border p-4 [overflow-wrap:anywhere] break-words">
      <label className="flex min-h-11 cursor-pointer items-start gap-3">
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
          <span className="text-muted-foreground mt-1 block text-base">
            {item.theater || "剧场未注明"} · {item.language}
          </span>
        </span>
      </label>
      <section className="mt-3 space-y-1 text-base" aria-label="可核实事实">
        <h3 className="font-medium">可核实事实</h3>
        {facts && (
          <p className="text-muted-foreground">
            {itemFactsLine(
              facts,
              result.conditions.channel &&
                facts.channel_rules[result.conditions.channel] !== "allowed"
                ? result.conditions.channel
                : undefined,
            )}
          </p>
        )}
        {checks.availability && <p>{checks.availability}</p>}
        {checks.posted && <p>{checks.posted}</p>}
        {!facts && (
          <p className="text-muted-foreground">
            标签、上架日期与渠道资料尚未读取
          </p>
        )}
      </section>
      {checks.pending.length > 0 && (
        <section className="mt-3 space-y-1 text-base" aria-label="待核实事项">
          <h3 className="font-medium">待核实事项</h3>
          {checks.pending.map((warning) => (
            <p key={warning} className="text-warning-ink">
              {warning}
            </p>
          ))}
        </section>
      )}
      <section className="mt-3 space-y-1 text-base" aria-label="入选依据">
        <h3 className="font-medium">入选依据</h3>
        <p>{item.reason}</p>
        <p
          data-testid="pick-primary-evidence"
          className="bg-muted/50 rounded-md p-2 text-base leading-6"
        >
          {primary ? (
            <>
              {evidenceLine(primary)}
              <br />
              依据日期：{evidenceDate(primary.observed_at)}
            </>
          ) : (
            "暂无匹配的榜单或指标依据 · 依据日期未知"
          )}
        </p>
      </section>
      {checkHref && (
        <p className="mt-2">
          <RowCheckLink href={checkHref} title={item.title} />
        </p>
      )}
      {feedback !== undefined && (
        <FeedbackEvidence item={feedback ?? undefined} />
      )}
      <EvidenceDetails item={item} />
    </article>
  );
}

/** Historical timing notes and hot-source scope for this result, shown once. */
function ResultNotices({ notes }: { notes: PickNotesState | undefined }) {
  if (notes?.kind === "gone" || notes?.kind === "error")
    return (
      <p className="text-muted-foreground text-base" data-testid="pick-notes">
        {notes.kind === "gone" ? notes.message : "依据说明暂不可用，可稍后刷新"}
      </p>
    );
  if (notes?.kind !== "notes") return null;
  const { data_notices: notices = [], hot_scope: hot } = notes.notes;
  if (
    notices.length === 0 &&
    !hot &&
    notes.notes.notices_reference_at === undefined
  )
    return null;
  return (
    <div className="space-y-1 text-base" data-testid="pick-notes">
      <p className="font-medium">{notesReferenceLine(notes.notes)}</p>
      {[...new Set(notices)].map((notice) => (
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
      <p className="rounded-lg border border-dashed p-6 text-base">
        没有符合这次条件的剧目，可以放宽条件后重新查询。
      </p>
    );
  const { lead, steps, caution } = zeroDiagnosisLines(diagnosis);
  return (
    <div
      className="rounded-lg border border-dashed p-6 text-base"
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
  const complete = result.run_status === "success";
  const replayHref = replayLink(result);
  const read = notes?.kind === "notes" ? notes.notes : undefined;
  return (
    <div className="space-y-4 text-base leading-6">
      <p role="status" className="text-base">
        {pickRunStatusLabel[result.run_status]}
      </p>
      <div className="flex flex-wrap items-center justify-between gap-3 border-b pb-3">
        <div>
          <h2 className="font-semibold">本次候选</h2>
          <p className="text-muted-foreground text-base">
            {complete
              ? `找到 ${result.items.length} 部 / 请求 ${result.conditions.limit} 部`
              : `本次查询未完成，尚不能确认符合条件的数量（已收到 ${result.items.length} 部候选）`}
            {complete &&
              typeof result.matched_total === "number" &&
              ` · 符合条件共 ${result.matched_total} 部`}
          </p>
        </div>
        {!readOnly && (
          <Button
            size="sm"
            className="min-h-11 shrink-0 text-base"
            disabled={
              busy || selected.length === 0 || result.run_status !== "success"
            }
            onClick={onSave}
          >
            {busy ? "保存中…" : `保存选中（${selected.length}）`}
          </Button>
        )}
      </div>
      <p className="text-muted-foreground text-base">
        {conditionsLine(result.conditions)}
      </p>
      <p
        className="text-muted-foreground text-base"
        data-testid="pick-data-as-of"
      >
        数据截至：{dataAsOfLine(result.data_as_of)}
      </p>
      <section
        aria-label="查询时的版本快照"
        className="text-foreground min-w-0 space-y-1 text-base [overflow-wrap:anywhere]"
      >
        <h3 className="font-medium">查询时的版本快照</h3>
        <p>以下版本属于这份候选生成时的快照，不代表当前执行许可。</p>
        <p>剧库批次：{result.catalog_batch_id}</p>
        <p>知识批次：{result.knowledge_batch_id ?? "未使用"}</p>
        <p>筛选规则：{result.rule_version}</p>
        <p>排序规则：{result.ranking_version}</p>
      </section>
      <ResultNotices notes={notes} />
      {read?.feedback && <FeedbackSummary feedback={read.feedback} />}
      {replayHref && <ReplayLink href={replayHref} />}
      {complete && result.items.length === 0 && <EmptyResult notes={read} />}
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
          facts={read?.item_facts[item.item_id]}
          feedback={
            read?.feedback
              ? (read.feedback.items.find(
                  (entry) => entry.key === item.identity,
                ) ?? null)
              : undefined
          }
        />
      ))}
      <p className="text-muted-foreground text-base">
        这是查询时的资料快照。推荐不代表已经发布或同步到外部系统。
      </p>
    </div>
  );
}
