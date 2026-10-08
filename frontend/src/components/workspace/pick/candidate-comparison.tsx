"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Textarea } from "@/components/ui/textarea";
import {
  getPickResult,
  getSaveReceipt,
  listPickResults,
  PickApiError,
  savePickSelection,
  type SaveCommand,
  type SaveReceipt,
} from "@/core/pick/api";
import {
  alignCandidates,
  selectionGroups,
  type ComparisonChoices,
  type ComparisonRow,
  type ComparisonSource,
} from "@/core/pick/comparison";
import {
  conditionsLine,
  dataAsOfLine,
  day,
  evidenceDate,
  evidenceLine,
  saveReceiptLine,
} from "@/core/pick/format";
import type { PickResult } from "@/core/pick/types";
import { cn } from "@/lib/utils";

import { usePickContext } from "./pick-context";

const selectClass =
  "border-line bg-surface min-h-11 w-full min-w-0 rounded-md border px-3 text-base focus-visible:outline-2 focus-visible:outline-ring";

export function CandidateComparisonLauncher({
  threadId,
}: {
  threadId: string;
}) {
  const [open, setOpen] = useState(false);
  const [pending, setPending] = useState(false);
  const changeOpen = (next: boolean) => {
    if (
      !next &&
      pending &&
      !window.confirm(
        "仍有保存结果未确认。关闭会丢失本页的重试操作，请先查看我的选剧。仍要关闭吗？",
      )
    )
      return;
    setOpen(next);
    if (!next) setPending(false);
  };
  return (
    <Dialog open={open} onOpenChange={changeOpen}>
      <DialogTrigger asChild>
        <Button variant="outline" className="min-h-11">
          比较两批候选
        </Button>
      </DialogTrigger>
      <DialogContent
        showCloseButton={false}
        className="max-h-[92dvh] overflow-y-auto p-4 text-base leading-6 sm:max-w-6xl md:p-6"
      >
        <div className="flex items-center justify-between gap-3">
          <DialogTitle>比较两批候选</DialogTitle>
          <DialogClose asChild>
            <Button variant="ghost" className="min-h-11">
              关闭比较
            </Button>
          </DialogClose>
        </div>
        <DialogDescription>
          明确选择两批候选，再为每部剧选择采用哪份来源。比较本身不改变对话引用。
        </DialogDescription>
        <CandidateComparison
          threadId={threadId}
          onUseBatch={() => changeOpen(false)}
          onPending={setPending}
        />
      </DialogContent>
    </Dialog>
  );
}

export function CandidateComparison({
  threadId,
  onUseBatch,
  onPending,
}: {
  threadId: string;
  onUseBatch: () => void;
  onPending?: (pending: boolean) => void;
}) {
  const pick = usePickContext()!;
  const [firstId, setFirstId] = useState("");
  const [secondId, setSecondId] = useState("");
  const [locked, setLocked] = useState(false);
  const list = useQuery({
    queryKey: ["pick-results", pick.ownerId, threadId],
    queryFn: ({ signal }) => listPickResults(threadId, signal),
    enabled: pick.ownerId !== "anonymous",
    retry: false,
  });
  const results =
    list.data?.filter(
      (result) =>
        result.thread_id === threadId && result.run_status === "success",
    ) ?? [];
  return (
    <div className="min-w-0 space-y-4 text-base leading-6 [overflow-wrap:anywhere]">
      <div className="grid gap-4 md:grid-cols-2">
        {(
          [
            ["第一批候选", firstId, setFirstId, secondId],
            ["第二批候选", secondId, setSecondId, firstId],
          ] as const
        ).map(([label, value, setValue, other]) => (
          <label key={label} className="block">
            {label}
            <select
              className={selectClass}
              disabled={locked}
              value={value}
              onChange={(event) => setValue(event.target.value)}
            >
              <option value="">请选择候选批次</option>
              {results.map((result) => (
                <option
                  key={result.id}
                  value={result.id}
                  disabled={result.id === other}
                >
                  {day(result.created_at)} · {conditionsLine(result.conditions)}{" "}
                  · {result.id}
                </option>
              ))}
            </select>
          </label>
        ))}
      </div>
      {list.isPending && <p role="status">正在读取当前对话的候选批次…</p>}
      {list.isError && (
        <div role="alert">
          <p>候选批次读取失败，请重试。</p>
          <Button
            variant="outline"
            className="min-h-11"
            onClick={() => void list.refetch()}
          >
            重新读取候选批次
          </Button>
        </div>
      )}
      {list.isSuccess && results.length < 2 && (
        <p>当前对话还没有两批已完成的候选。请先继续选剧。</p>
      )}
      {firstId && secondId && (
        <LoadedComparison
          key={`${pick.ownerId}:${threadId}:${firstId}:${secondId}`}
          threadId={threadId}
          firstId={firstId}
          secondId={secondId}
          onUseBatch={onUseBatch}
          locked={locked}
          onLock={(next) => {
            setLocked(next);
            onPending?.(next);
          }}
        />
      )}
    </div>
  );
}

function BatchContext({
  result,
  label,
}: {
  result: PickResult;
  label: string;
}) {
  return (
    <section
      className="border-line min-w-0 space-y-2 rounded-md border p-4"
      aria-label={`${label}上下文`}
    >
      <h3 className="text-lg font-semibold">{label}</h3>
      <p>查询于 {day(result.created_at)}</p>
      <p>{conditionsLine(result.conditions)}</p>
      <p>{dataAsOfLine(result.data_as_of)}</p>
      <p>
        镜像版本：{result.data_as_of?.mirror_version ?? "未记录，无法完整回放"}
      </p>
      <p>
        筛选规则：{result.rule_version} · 排序规则：{result.ranking_version}
      </p>
      <details>
        <summary className="min-h-11 cursor-pointer py-2">
          查看批次和完整查询条件
        </summary>
        <p>候选批次：{result.id}</p>
        <p>剧库批次：{result.catalog_batch_id}</p>
        <p>知识批次：{result.knowledge_batch_id ?? "未使用"}</p>
        <pre className="text-sm whitespace-pre-wrap">
          {JSON.stringify(result.conditions, null, 2)}
        </pre>
        {result.observations && (
          <pre className="text-sm whitespace-pre-wrap">
            {JSON.stringify(result.observations, null, 2)}
          </pre>
        )}
      </details>
    </section>
  );
}

function SourceEvidence({ source }: { source: ComparisonSource | undefined }) {
  if (!source) return <p>本批未包含</p>;
  const item = source.item;
  return (
    <div className="space-y-2">
      <p className="font-medium">{item.title}</p>
      <p>
        {item.theater} · {item.language}
      </p>
      <p>{item.reason}</p>
      {item.warnings.map((warning) => (
        <p key={warning} className="text-warning-ink">
          {warning}
        </p>
      ))}
      <details>
        <summary className="min-h-11 cursor-pointer py-2">
          查看来源证据（{item.evidence.length}）
        </summary>
        <p>稳定身份：{item.identity}</p>
        <p>
          来源候选：{source.result_id} / {item.item_id}
        </p>
        {item.evidence.map((evidence) => (
          <div
            key={evidence.citation_id}
            className="border-line my-2 border-l-2 pl-3"
          >
            <p>{evidenceLine(evidence)}</p>
            <p>
              {evidenceDate(evidence.observed_at)} · {evidence.source_ref}
            </p>
          </div>
        ))}
      </details>
    </div>
  );
}

function ComparisonItem({
  row,
  choice,
  onChoose,
}: {
  row: ComparisonRow;
  choice: ComparisonChoices[string];
  onChoose: (side: ComparisonChoices[string]) => void;
}) {
  const [detail, setDetail] = useState<"first" | "second">(
    row.first ? "first" : "second",
  );
  const title = (row.first ?? row.second)!.item.title;
  return (
    <tr className="border-line block border-b py-4 md:table-row">
      <th
        scope="row"
        className="block p-3 text-left align-top font-normal md:table-cell md:w-1/3"
      >
        <p className="font-semibold">{title}</p>
        {row.ambiguousTitle && (
          <p className="text-warning-ink">同名身份不同，待核对；分别保留</p>
        )}
        <label className="mt-2 block">
          采用来源：{title}
          <select
            className={selectClass}
            aria-label={`采用来源：${title}${row.ambiguousTitle ? `（${row.identity}）` : ""}`}
            value={choice ?? ""}
            onChange={(event) =>
              onChoose(
                event.target.value === "first" ||
                  event.target.value === "second"
                  ? event.target.value
                  : undefined,
              )
            }
          >
            <option value="">不选择</option>
            {row.first && <option value="first">第一批来源</option>}
            {row.second && <option value="second">第二批来源</option>}
          </select>
        </label>
        <label className="mt-3 block md:hidden">
          查看哪批详情：{title}
          <select
            className={selectClass}
            value={detail}
            onChange={(event) =>
              setDetail(event.target.value as "first" | "second")
            }
          >
            <option value="first">第一批详情</option>
            <option value="second">第二批详情</option>
          </select>
        </label>
      </th>
      <td
        className={cn(
          "p-3 align-top md:table-cell md:w-1/3",
          detail === "first" ? "block" : "hidden",
        )}
      >
        <SourceEvidence source={row.first} />
      </td>
      <td
        className={cn(
          "p-3 align-top md:table-cell md:w-1/3",
          detail === "second" ? "block" : "hidden",
        )}
      >
        <SourceEvidence source={row.second} />
      </td>
    </tr>
  );
}

function LoadedComparison({
  threadId,
  firstId,
  secondId,
  onUseBatch,
  onLock,
  locked,
}: {
  threadId: string;
  firstId: string;
  secondId: string;
  locked: boolean;
  onUseBatch: () => void;
  onLock: (locked: boolean) => void;
}) {
  const pick = usePickContext()!;
  const [choices, setChoices] = useState<ComparisonChoices>({});
  const query = useQuery({
    queryKey: ["pick-comparison", pick.ownerId, threadId, firstId, secondId],
    queryFn: async ({ signal }) => {
      const [first, second] = await Promise.all([
        getPickResult(firstId, signal),
        getPickResult(secondId, signal),
      ]);
      if (first.id !== firstId || second.id !== secondId)
        throw new Error("来源批次不一致");
      return { first, second, rows: alignCandidates(threadId, first, second) };
    },
    staleTime: Infinity,
    retry: false,
  });
  if (query.isPending) return <p role="status">正在读取两批历史快照…</p>;
  if (query.isError)
    return (
      <div role="alert">
        <p>无法核实两批候选的来源或运行状态，请重新读取。</p>
        <Button
          className="min-h-11"
          variant="outline"
          onClick={() => void query.refetch()}
        >
          重新读取比较
        </Button>
      </div>
    );
  const { first, second, rows } = query.data;
  const groups = selectionGroups(rows, choices);
  return (
    <div className="space-y-4">
      <div className="grid gap-4 md:grid-cols-2">
        <BatchContext result={first} label="第一批" />
        <BatchContext result={second} label="第二批" />
      </div>
      <p>
        两批保留各自的条件、资料时点和规则；期次或规则不同不表示哪批更优。相同稳定身份只选一次，采用来源由你指定。
      </p>
      <table className="block w-full table-fixed md:table">
        <caption className="py-2 text-left">
          候选来源比较（{rows.length} 个独立身份）
        </caption>
        <thead className="hidden md:table-header-group">
          <tr>
            <th scope="col" className="p-3 text-left">
              选择与来源
            </th>
            <th scope="col" className="p-3 text-left">
              第一批依据
            </th>
            <th scope="col" className="p-3 text-left">
              第二批依据
            </th>
          </tr>
        </thead>
        {(
          [
            ["仅第一批", rows.filter((row) => row.first && !row.second)],
            ["两批共有", rows.filter((row) => row.first && row.second)],
            ["仅第二批", rows.filter((row) => !row.first && row.second)],
          ] as const
        ).map(([label, section]) => (
          <tbody key={label} className="block md:table-row-group">
            <tr className="block md:table-row">
              <th
                colSpan={3}
                scope="rowgroup"
                className="bg-raised block p-3 text-left md:table-cell"
              >
                {label}（{section.length}）
              </th>
            </tr>
            {section.map((row) => (
              <ComparisonItem
                key={row.identity}
                row={row}
                choice={choices[row.identity]}
                onChoose={(side) =>
                  setChoices((old) => ({ ...old, [row.identity]: side }))
                }
              />
            ))}
          </tbody>
        ))}
      </table>
      <p>
        已选择{" "}
        {groups.reduce((count, group) => count + group.item_ids.length, 0)}{" "}
        部，各剧保留所选批次依据。
      </p>
      <ComparisonSave groups={groups} firstId={first.id} onLock={onLock} />
      <div className="flex flex-wrap gap-3">
        {(
          [
            [first, "第一批"],
            [second, "第二批"],
          ] as const
        ).map(([result, label]) => (
          <Button
            key={result.id}
            className="min-h-11"
            variant="outline"
            disabled={locked}
            onClick={() => {
              pick.show(
                result,
                groups.find((group) => group.result_id === result.id)
                  ?.item_ids ?? [],
              );
              onUseBatch();
            }}
          >
            以{label}继续对话
          </Button>
        ))}
      </div>
      <p className="text-helper">
        继续对话仅引用指定的一批；该批未选择条目时引用整批。
      </p>
    </div>
  );
}

type SaveAttempt = {
  key: string;
  command: SaveCommand;
  receipt?: SaveReceipt;
  error?: string;
};

function ComparisonSave({
  groups,
  firstId,
  onLock,
}: {
  groups: ReturnType<typeof selectionGroups>;
  firstId: string;
  onLock: (locked: boolean) => void;
}) {
  const pick = usePickContext()!;
  const client = useQueryClient();
  const [note, setNote] = useState("");
  const [attempts, setAttempts] = useState<SaveAttempt[]>([]);
  const [busy, setBusy] = useState(false);
  const inFlight = useRef<AbortController | null>(null);
  const active = useRef(true);
  const records = useRef<SaveAttempt[]>([]);
  useEffect(() => {
    active.current = true;
    return () => {
      active.current = false;
      inFlight.current?.abort();
    };
  }, []);
  const save = async (retry?: SaveAttempt) => {
    if (inFlight.current || (!retry && groups.length === 0)) return;
    const pending = retry
      ? [retry]
      : groups.map((group) => {
          const key = JSON.stringify([
            group.result_id,
            [...group.item_ids].sort(),
            note,
          ]);
          const existing = records.current.find(
            (attempt) => attempt.key === key,
          );
          if (existing) return existing;
          const attempt: SaveAttempt = {
            key,
            command: { ...group, note, request_id: crypto.randomUUID() },
          };
          records.current.push(attempt);
          return attempt;
        });
    const controller = new AbortController();
    inFlight.current = controller;
    setBusy(true);
    onLock(true);
    setAttempts([...records.current]);
    try {
      for (const attempt of pending) {
        if (attempt.receipt || controller.signal.aborted) continue;
        try {
          let receipt: SaveReceipt | null = null;
          if (attempt.error)
            receipt = await getSaveReceipt(
              attempt.command.request_id,
              controller.signal,
            );
          if (!receipt) {
            try {
              receipt = await savePickSelection(
                attempt.command,
                controller.signal,
              );
            } catch (error) {
              if (controller.signal.aborted) return;
              if (error instanceof PickApiError && error.status < 500)
                throw error;
              receipt = await getSaveReceipt(
                attempt.command.request_id,
                controller.signal,
              );
            }
          }
          if (!active.current || controller.signal.aborted) return;
          if (receipt?.request_id !== attempt.command.request_id)
            throw new Error("unknown outcome");
          attempt.receipt = receipt;
          attempt.error = undefined;
          void client.invalidateQueries({
            queryKey: ["pick-selections", pick.ownerId],
          });
        } catch (error) {
          if (!active.current || controller.signal.aborted) return;
          attempt.error =
            error instanceof PickApiError && error.status < 500
              ? error.message
              : "保存结果尚未确认，请使用原操作重试";
        }
        if (active.current) setAttempts([...records.current]);
      }
    } finally {
      if (active.current) {
        setBusy(false);
        inFlight.current = null;
        onLock(records.current.some((attempt) => !attempt.receipt));
      }
    }
  };
  const count = groups.reduce(
    (total, group) => total + group.item_ids.length,
    0,
  );
  return (
    <section
      aria-label="比较保存"
      className="border-line space-y-3 border-t pt-4"
    >
      <label className="block">
        比较保存备注
        <Textarea
          value={note}
          disabled={busy}
          maxLength={2000}
          className="mt-2 text-base md:text-base"
          onChange={(event) => setNote(event.target.value)}
        />
      </label>
      <p>
        按来源批次分别保存并返回回执；某批未确认不会撤销其他批的保存。已有选剧保留原来源及备注。
      </p>
      <Button
        className="min-h-11"
        disabled={busy || count === 0}
        onClick={() => void save()}
      >
        保存所选来源（{count} 部）
      </Button>
      {attempts.map((attempt) => {
        const label =
          attempt.command.result_id === firstId ? "第一批" : "第二批";
        return (
          <div key={attempt.command.request_id} className="space-y-2">
            {attempt.receipt ? (
              <p role="status">
                {label}：{saveReceiptLine(attempt.receipt.saved)}
              </p>
            ) : attempt.error ? (
              <>
                <p role="alert">
                  {label}：{attempt.error}
                </p>
                <Button
                  variant="outline"
                  className="min-h-11"
                  disabled={busy}
                  onClick={() => void save(attempt)}
                >
                  重试{label}保存
                </Button>
              </>
            ) : (
              <p role="status">{label}：正在保存…</p>
            )}
            <details>
              <summary className="min-h-11 cursor-pointer py-2">
                {label}操作来源与回执
              </summary>
              <p>候选：{attempt.command.result_id}</p>
              <p>选择：{attempt.command.item_ids.join("、")}</p>
              <p>备注：{attempt.command.note || "无"}</p>
              <p>操作编号：{attempt.command.request_id}</p>
            </details>
          </div>
        );
      })}
      {attempts.some((attempt) => attempt.receipt) && (
        <Link
          className="text-link inline-flex min-h-11 items-center hover:underline"
          href="/workspace/picks"
          target={
            attempts.some((attempt) => !attempt.receipt) ? "_blank" : undefined
          }
          rel="noopener noreferrer"
        >
          查看我的选剧
          {attempts.some((attempt) => !attempt.receipt) ? "（新标签页）" : ""}
        </Link>
      )}
    </section>
  );
}
