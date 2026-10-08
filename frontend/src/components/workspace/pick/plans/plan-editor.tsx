"use client";

import Link from "next/link";
import { useEffect, useRef, useState, type ReactNode } from "react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import {
  Sheet,
  SheetContent,
  SheetTitle,
  SheetDescription,
} from "@/components/ui/sheet";
import { Textarea } from "@/components/ui/textarea";
import { PickApiError } from "@/core/pick/api";
import {
  downloadPlanExport,
  exportPlan,
  getPlan,
  previewPlan,
  updatePlan,
} from "@/core/pick/completion-api";
import {
  type Plan,
  type PlanExport,
  type PlanPreview,
  type PlanRowInput,
  type PlanUpdate,
} from "@/core/pick/completion-types";
import { replayHref } from "@/core/pick/links";
import {
  editablePlan,
  localTimeChoices,
  timezonePreview,
} from "@/core/pick/plan-draft";
import { useIsMobile } from "@/hooks/use-mobile";

import { SourceResult } from "../my-selections";

import { usePlanDrafts } from "./plan-drafts";

const field =
  "min-h-11 w-full rounded-md border border-line bg-surface p-2 text-base";
export function PlanEditor({ initial }: { initial: Plan }) {
  const recovery = usePlanDrafts();
  const recovered = recovery?.drafts[initial.id];
  const [base, setBase] = useState(recovered?.base ?? initial);
  const [draft, setDraft] = useState(
    () => recovered?.draft ?? editablePlan(initial),
  );
  const [conflict, setConflict] = useState<Plan | null>(
    recovered && initial.version > recovered.base.version ? initial : null,
  );
  const [conflictTimeMode, setConflictTimeMode] =
    useState<PlanUpdate["timezone_change"]>(null);
  const [preview, setPreview] = useState<PlanPreview | null>(null);
  const [receipt, setReceipt] = useState<PlanExport | null>(null);
  const [departure, setDeparture] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [status, setStatus] = useState("");
  const [busy, setBusy] = useState(false);
  const editorTrigger = useRef<HTMLButtonElement | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const [zone, setZone] = useState(base.timezone);
  const [mode, setMode] = useState<PlanUpdate["timezone_change"]>(null);
  const [zonePreview, setZonePreview] = useState<PlanRowInput[] | null>(null);
  const [zoneChange, setZoneChange] = useState<PlanUpdate["timezone_change"]>(
    recovered?.timezoneChange ?? null,
  );
  const active = useRef(true);
  const controller = useRef<AbortController | null>(null);
  const pending = useRef<{ key: string; request_id: string } | null>(
    recovered?.pending ?? null,
  );
  const dirty = JSON.stringify(draft) !== JSON.stringify(editablePlan(base));
  if (initial.version > base.version && conflict?.version !== initial.version) {
    setZonePreview(null);
    setConflictTimeMode(null);
    if (dirty) setConflict(initial);
    else {
      setBase(initial);
      setDraft(editablePlan(initial));
      setZone(initial.timezone);
      setPreview(null);
      setZonePreview(null);
    }
  }
  const remember = recovery?.setDraft;
  useEffect(() => {
    remember?.(
      base.id,
      dirty
        ? { base, draft, timezoneChange: zoneChange, pending: pending.current }
        : null,
    );
  }, [base, draft, zoneChange, dirty, busy, remember]);
  useEffect(() => {
    active.current = true;
    return () => {
      active.current = false;
      controller.current?.abort();
    };
  }, []);
  useEffect(() => {
    if (!dirty) return;
    const unload = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    const leave = (event: MouseEvent) => {
      const link = (event.target as Element).closest?.("a[href]");
      if (
        !link ||
        link.getAttribute("target") === "_blank" ||
        link.getAttribute("href")?.startsWith("#")
      )
        return;
      if (
        event.metaKey ||
        event.ctrlKey ||
        event.shiftKey ||
        event.altKey ||
        event.button !== 0
      )
        return;
      event.preventDefault();
      event.stopPropagation();
      setDeparture(link.getAttribute("href"));
    };
    window.addEventListener("beforeunload", unload);
    document.addEventListener("click", leave, true);
    return () => {
      window.removeEventListener("beforeunload", unload);
      document.removeEventListener("click", leave, true);
    };
  }, [dirty, remember, base.id]);
  const edit = (next: typeof draft) => {
    setZonePreview(null);
    setDraft(next);
    setPreview(null);
    setStatus("");
  };
  const rowEdit = (rowId: string, patch: Partial<PlanRowInput>) =>
    edit({
      ...draft,
      rows: draft.rows.map((row) =>
        row.row_id === rowId ? { ...row, ...patch } : row,
      ),
    });
  const commandId = (body: unknown) => {
    const key = JSON.stringify(body);
    if (pending.current?.key !== key)
      pending.current = { key, request_id: crypto.randomUUID() };
    return pending.current.request_id;
  };
  const run = async (task: (signal: AbortSignal) => Promise<void>) => {
    if (busy) return;
    setBusy(true);
    setError("");
    controller.current = new AbortController();
    try {
      await task(controller.current.signal);
      return active.current;
    } catch (reason) {
      if (!active.current) return;
      setError(
        reason instanceof PickApiError
          ? reason.message
          : "操作结果尚未确认，请保留修改并重试原操作。",
      );
      if (
        reason instanceof PickApiError &&
        reason.code === "version_conflict"
      ) {
        setPreview(null);
        try {
          const current = await getPlan(base.id, controller.current.signal);
          if (active.current) {
            setConflict(current);
            setConflictTimeMode(null);
            setZonePreview(null);
          }
        } catch {
          if (active.current)
            setError("版本已变化，当前版本读取失败。请保留修改并重新读取。");
        }
      }
      return false;
    } finally {
      if (active.current) setBusy(false);
    }
  };
  const timeError = (row: PlanRowInput) => {
    if (!row.local_time) return null;
    const choices = localTimeChoices(row.local_time, draft.timezone);
    return !choices.length
      ? "此当地时间不存在或时区无效"
      : choices.length > 1 && row.fold === null
        ? "此当地时间出现两次，请选择 UTC 偏移"
        : null;
  };
  const invalid = draft.rows.some((row) => timeError(row));
  const save = () =>
    run(async (signal) => {
      const body = {
        ...draft,
        expected_version: base.version,
        timezone_change: zoneChange,
      };
      const saved = await updatePlan(
        base.id,
        { ...body, request_id: commandId(body) },
        signal,
      );
      if (!active.current) return;
      setBase(saved);
      setDraft(editablePlan(saved));
      setPreview(null);
      setConflict(null);
      setZoneChange(null);
      pending.current = null;
      setStatus(`已保存版本 ${saved.version}`);
    });
  const rebase = (keep: boolean) => {
    if (!conflict) return;
    const changedZone = draft.timezone !== conflict.timezone;
    if (keep && changedZone && !conflictTimeMode) return;
    setBase(conflict);
    if (!keep) {
      setDraft(editablePlan(conflict));
      setZone(conflict.timezone);
      setZoneChange(null);
    } else if (changedZone) {
      if (conflictTimeMode === "keep_instant") {
        const converted = timezonePreview(
          conflict,
          draft.timezone,
          "keep_instant",
        );
        setDraft({
          ...draft,
          rows: draft.rows.map((row) => {
            const time = converted.find((item) => item.row_id === row.row_id);
            return time
              ? { ...row, local_time: time.local_time, fold: time.fold }
              : row;
          }),
        });
      }
      setZoneChange(conflictTimeMode);
    } else setZoneChange(null);
    setConflict(null);
    setConflictTimeMode(null);
    pending.current = null;
    setError("");
    setPreview(null);
    setZonePreview(null);
  };
  const depart = (discard = true) => {
    if (!departure) return;
    const href = departure;
    remember?.(base.id, null);
    if (discard) setDraft(editablePlan(base));
    setDeparture(null);
    window.setTimeout(() => window.location.assign(href), 0);
  };
  return (
    <section className="mx-auto w-full max-w-6xl min-w-0 space-y-5 p-4 text-base leading-6 sm:p-6 [&_button]:text-base [&_input]:!text-base [&_textarea]:!text-base">
      <Dialog
        open={departure !== null}
        onOpenChange={(open) => {
          if (!open && !busy) setDeparture(null);
        }}
      >
        <DialogContent>
          <DialogTitle>计划有未保存修改</DialogTitle>
          <DialogDescription>
            选择留在本页、丢弃修改，或保存成功后继续。
          </DialogDescription>
          {error && <p role="alert">{error}</p>}
          <div className="flex flex-wrap gap-3">
            <Button
              className="min-h-11"
              variant="outline"
              disabled={busy}
              onClick={() => setDeparture(null)}
            >
              留在本页
            </Button>
            <Button
              className="min-h-11"
              variant="outline"
              disabled={busy}
              onClick={() => depart()}
            >
              丢弃修改并离开
            </Button>
            <Button
              className="min-h-11"
              disabled={busy || invalid || !!conflict || !draft.title.trim()}
              onClick={() =>
                void save().then((saved) => {
                  if (saved) depart(false);
                })
              }
            >
              保存并继续
            </Button>
          </div>
        </DialogContent>
      </Dialog>
      <header>
        <h1 className="text-2xl leading-8 font-bold">排期草稿</h1>
        <p>
          当前已保存版本 {base.version} · {base.timezone} ·{" "}
          {dirty ? "有未保存修改" : "已保存"}
        </p>
        <Link
          className="text-link inline-flex min-h-11 items-center underline"
          href="/workspace/pick-plans"
        >
          全部排期
        </Link>
      </header>
      {error && (
        <div role="alert" tabIndex={-1} className="text-danger-ink">
          {error}
        </div>
      )}
      {status && <p role="status">{status}</p>}
      {conflict && (
        <section className="border-warning-ink space-y-3 border p-4">
          <h2 className="text-lg">服务器版本 {conflict.version} 已变化</h2>
          <p>{conflict.title}</p>
          <p>服务器时区：{conflict.timezone}</p>
          <ul>
            {conflict.rows.map((row) => (
              <li key={row.row_id}>
                {row.title} · {row.account ?? "未填账号"} ·{" "}
                {row.local_time ?? "未填时间"} · 备注：{row.note || "无"}
              </li>
            ))}
          </ul>
          <p>本地输入仍保留。比较后决定采用哪一份。</p>
          {draft.timezone !== conflict.timezone && (
            <label className="block">
              时区冲突处理
              <select
                className={field}
                value={conflictTimeMode ?? ""}
                onChange={(event) =>
                  setConflictTimeMode(
                    event.target.value as PlanUpdate["timezone_change"],
                  )
                }
              >
                <option value="">请明确选择</option>
                <option value="keep_local_time">保留本地时区与当地时间</option>
                <option value="keep_instant">
                  保留服务器时刻，采用本地其他字段
                </option>
              </select>
            </label>
          )}
          <Button
            className="min-h-11"
            disabled={draft.timezone !== conflict.timezone && !conflictTimeMode}
            onClick={() => rebase(true)}
          >
            保留本地修改，以新版本继续
          </Button>
          <Button
            className="min-h-11"
            variant="outline"
            onClick={() => rebase(false)}
          >
            采用服务器版本
          </Button>
        </section>
      )}
      <fieldset disabled={busy} className="min-w-0 space-y-4">
        <label className="block">
          计划名称
          <Input
            className="min-h-11 text-base"
            value={draft.title}
            onChange={(event) => edit({ ...draft, title: event.target.value })}
          />
        </label>
        <details>
          <summary className="min-h-11 cursor-pointer">
            更改计划时区（当前 {draft.timezone}）
          </summary>
          <div className="space-y-3 py-3">
            <label className="block">
              新时区（IANA）
              <Input
                className="min-h-11"
                value={zone}
                onChange={(event) => {
                  setZone(event.target.value);
                  setZonePreview(null);
                }}
              />
            </label>
            <label className="block">
              时间保留方式
              <select
                className={field}
                value={mode ?? ""}
                onChange={(event) => {
                  setMode(event.target.value as PlanUpdate["timezone_change"]);
                  setZonePreview(null);
                }}
              >
                <option value="">请选择</option>
                <option value="keep_local_time">保留当地时间</option>
                <option value="keep_instant">保留同一时刻</option>
              </select>
            </label>
            <Button
              variant="outline"
              className="min-h-11"
              disabled={!mode || dirty}
              onClick={() => {
                try {
                  setZonePreview(timezonePreview(base, zone, mode!));
                } catch {
                  setError("时区无效，请检查 IANA 时区名称。");
                }
              }}
            >
              预览时区变化
            </Button>
            {dirty && <p>请先保存当前修改，再预览时区变化。</p>}
            {zonePreview && (
              <div>
                <ul>
                  {zonePreview.map((row) => (
                    <li key={row.row_id}>
                      {base.rows.find((r) => r.row_id === row.row_id)?.title}：
                      {row.local_time ?? "时间未填"}（{zone}）
                    </li>
                  ))}
                </ul>
                <Button
                  className="min-h-11"
                  onClick={() => {
                    edit({ ...draft, timezone: zone, rows: zonePreview });
                    setZoneChange(mode);
                    setZonePreview(null);
                  }}
                >
                  确认时区变化
                </Button>
              </div>
            )}
          </div>
        </details>
        <div className="overflow-x-auto">
          <table className="w-full text-left">
            <caption className="pb-3 text-left">
              共 {draft.rows.length} 行；未填执行字段可以保留为草稿
            </caption>
            <thead className="hidden sm:table-header-group">
              <tr>
                <th className="p-2">剧目与来源</th>
                <th className="p-2">账号 / 渠道</th>
                <th className="p-2">当地时间 / 时区</th>
                <th className="p-2">操作</th>
              </tr>
            </thead>
            <tbody>
              {draft.rows.map((row) => {
                const source = base.rows.find(
                  (item) => item.row_id === row.row_id,
                );
                return (
                  <tr
                    id={`row-${row.row_id}`}
                    key={row.row_id}
                    tabIndex={-1}
                    className="block border-t sm:table-row"
                  >
                    <td className="block p-2 sm:table-cell">
                      <strong className="break-words">
                        {source?.title ?? row.identity}
                      </strong>
                      <p>
                        {source?.theater} · {source?.language}
                      </p>
                      <details>
                        <summary className="min-h-11 cursor-pointer">
                          原始依据
                        </summary>
                        <p className="break-all">
                          批次 {source?.source_pin.catalog_batch_id} · 规则{" "}
                          {source?.source_pin.rule_version} · 镜像{" "}
                          {source?.source_pin.mirror_version ?? "无"}
                        </p>
                        <Link
                          className="text-link inline-flex min-h-11 items-center underline"
                          target="_blank"
                          href={replayHref(
                            row.source_result_id,
                            source?.source_pin.mirror_version ?? null,
                          )}
                        >
                          查看来源（新标签页）
                        </Link>
                        {source?.source_pin.mirror_version == null && (
                          <p>
                            原始镜像版本未保留；资料页不能完整重现当时来源。请以历史候选快照为准。
                          </p>
                        )}
                        <SourceResult id={row.source_result_id} />
                      </details>
                    </td>
                    <td className="block p-2 sm:table-cell">
                      {row.account ?? "账号未填"} / {row.channel ?? "渠道未填"}
                    </td>
                    <td className="block p-2 sm:table-cell">
                      {row.local_time ?? "时间未填"}
                      <p>{draft.timezone}</p>
                      {timeError(row) && (
                        <p className="text-danger-ink">{timeError(row)}</p>
                      )}
                    </td>
                    <td className="block p-2 sm:table-cell">
                      <Button
                        variant="outline"
                        className="min-h-11"
                        onClick={(event) => {
                          editorTrigger.current = event.currentTarget;
                          setEditing(
                            editing === row.row_id ? null : row.row_id,
                          );
                        }}
                      >
                        编辑 {source?.title ?? "计划行"}
                      </Button>
                      <Button
                        variant="ghost"
                        className="text-ink-1 min-h-11"
                        onClick={() => {
                          if (window.confirm("从本草稿移除此行？保存后生效。"))
                            edit({
                              ...draft,
                              rows: draft.rows.filter(
                                (item) => item.row_id !== row.row_id,
                              ),
                            });
                        }}
                      >
                        移除行
                      </Button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        {editing &&
          (() => {
            const row = draft.rows.find((item) => item.row_id === editing);
            if (!row) return null;
            const options = row.local_time
              ? localTimeChoices(row.local_time, draft.timezone)
              : [];
            return (
              <RowEditorSurface
                close={() => setEditing(null)}
                returnFocus={() => editorTrigger.current?.focus()}
              >
                <h2 className="text-lg">
                  编辑{" "}
                  {base.rows.find((item) => item.row_id === editing)?.title}
                </h2>
                <label className="block">
                  发布账号
                  <Input
                    className="min-h-11"
                    value={row.account ?? ""}
                    onChange={(event) =>
                      rowEdit(editing, { account: event.target.value || null })
                    }
                  />
                </label>
                <label className="block">
                  发布渠道
                  <select
                    className={field}
                    value={row.channel ?? ""}
                    onChange={(event) =>
                      rowEdit(editing, {
                        channel:
                          event.target.value === ""
                            ? null
                            : (event.target.value as PlanRowInput["channel"]),
                      })
                    }
                  >
                    <option value="">未填写</option>
                    <option value="youtube">YouTube</option>
                    <option value="tiktok">TikTok</option>
                    <option value="facebook">Facebook</option>
                  </select>
                </label>
                <label className="block">
                  当地发布时间（{draft.timezone}）
                  <input
                    disabled={zoneChange === "keep_instant"}
                    type="datetime-local"
                    className={field}
                    value={row.local_time ?? ""}
                    aria-invalid={!!timeError(row)}
                    aria-describedby={
                      timeError(row) ? "local-time-error" : undefined
                    }
                    onChange={(event) =>
                      rowEdit(editing, {
                        local_time: event.target.value || null,
                        fold: null,
                      })
                    }
                  />
                </label>
                {zoneChange === "keep_instant" && (
                  <p>请先保存保留同一时刻的时区变更，再编辑当地时间。</p>
                )}
                {timeError(row) && (
                  <p id="local-time-error" role="alert">
                    {timeError(row)}
                  </p>
                )}
                {options.length > 1 && (
                  <label className="block">
                    重复时间的 UTC 偏移
                    <select
                      className={field}
                      disabled={zoneChange === "keep_instant"}
                      value={row.fold ?? ""}
                      onChange={(event) =>
                        rowEdit(editing, {
                          fold:
                            event.target.value === ""
                              ? null
                              : Number(event.target.value),
                        })
                      }
                    >
                      <option value="">请选择</option>
                      {options.map((choice) => (
                        <option key={choice.fold} value={choice.fold}>
                          {choice.offset}（
                          {choice.fold === 0 ? "第一次" : "第二次"}）
                        </option>
                      ))}
                    </select>
                  </label>
                )}
                <label className="block">
                  发布文案
                  <Textarea
                    className="text-base"
                    value={row.copy_text}
                    onChange={(event) =>
                      rowEdit(editing, { copy_text: event.target.value })
                    }
                  />
                </label>
                <label className="block">
                  个人备注
                  <Textarea
                    className="text-base"
                    value={row.note}
                    onChange={(event) =>
                      rowEdit(editing, { note: event.target.value })
                    }
                  />
                </label>
                <Button
                  className="min-h-11"
                  variant="outline"
                  onClick={() => setEditing(null)}
                >
                  收起编辑（修改保留，尚未保存）
                </Button>
              </RowEditorSurface>
            );
          })()}
      </fieldset>
      <div className="flex flex-wrap gap-3">
        <Button
          className="min-h-11"
          disabled={
            busy || !dirty || !!conflict || invalid || !draft.title.trim()
          }
          onClick={() => void save()}
        >
          {busy ? "处理中…" : "保存计划"}
        </Button>
        <Button
          variant="outline"
          className="min-h-11"
          disabled={busy || dirty || !!conflict || !draft.rows.length}
          onClick={() =>
            void run(async (signal) => {
              const body = { expected_version: base.version };
              const result = await previewPlan(
                base.id,
                {
                  ...body,
                  request_id: commandId({ operation: "preview", ...body }),
                },
                signal,
              );
              if (active.current) {
                setPreview(result);
                setStatus("执行条件核对完成");
                pending.current = null;
              }
            })
          }
        >
          核对执行条件
        </Button>
      </div>
      {preview && (
        <section className="space-y-3 border-t pt-4">
          <h2 className="text-lg">版本 {preview.plan.version} 核对结果</h2>
          <p>
            核对时间 {preview.checked_at} · 阻断{" "}
            {
              preview.checks.filter((check) => check.status === "blocked")
                .length
            }{" "}
            行
          </p>
          {preview.checks.map((check) => (
            <div key={check.row_id} className="border-b py-2">
              <a
                className="text-link inline-flex min-h-11 items-center underline"
                href={`#row-${check.row_id}`}
              >
                定位{" "}
                {base.rows.find((row) => row.row_id === check.row_id)?.title ??
                  check.row_id}
              </a>
              <p>{check.status === "ready" ? "可导出" : "需处理"}</p>
              {check.blockers.map((text, index) => (
                <p className="text-danger-ink" key={index}>
                  {text}
                </p>
              ))}
              {check.warnings.map((text, index) => (
                <p className="text-warning-ink" key={index}>
                  提醒：{text}
                </p>
              ))}
              <p>
                本次规则 {check.current_pin?.rule_version ?? "必要来源未取得"} ·
                本次镜像 {check.current_pin?.mirror_version ?? "未知"}
              </p>
            </div>
          ))}
          {preview.exportable &&
            !preview.checks.some((check) => check.status === "blocked") &&
            !dirty && (
              <Button
                className="min-h-11"
                disabled={busy || receipt?.preview_id === preview.preview_id}
                onClick={() =>
                  void run(async (signal) => {
                    const body = {
                      expected_version: base.version,
                      preview_id: preview.preview_id,
                    };
                    const result = await exportPlan(
                      base.id,
                      {
                        ...body,
                        request_id: commandId({ operation: "export", ...body }),
                      },
                      signal,
                    );
                    if (active.current) {
                      setReceipt(result);
                      pending.current = null;
                      setStatus(
                        `执行表已生成，版本 ${result.plan_version}；尚未发布`,
                      );
                    }
                  })
                }
              >
                确认版本 {preview.plan.version}，生成执行表
              </Button>
            )}
        </section>
      )}
      {receipt && (
        <section className="space-y-2 border-t pt-4">
          <h2 className="text-lg">
            执行表已生成 · 版本 {receipt.plan_version}
          </h2>
          <p>
            {receipt.filename} · {receipt.row_count} 行 · 尚未发布
          </p>
          <Button
            variant="outline"
            className="min-h-11"
            disabled={busy}
            onClick={() =>
              void run(async (signal) => {
                await downloadPlanExport(receipt, signal);
                if (active.current)
                  setStatus("已发起下载；文件仍可用同一回执重新下载。");
              })
            }
          >
            下载同一执行表
          </Button>
        </section>
      )}
    </section>
  );
}

function RowEditorSurface({
  children,
  close,
  returnFocus,
}: {
  children: ReactNode;
  close: () => void;
  returnFocus: () => void;
}) {
  const mobile = useIsMobile();
  const desktop = useRef<HTMLElement | null>(null);
  useEffect(() => {
    if (!mobile)
      desktop.current?.querySelector<HTMLInputElement>("input")?.focus();
  }, [mobile]);
  if (mobile)
    return (
      <Sheet
        open
        onOpenChange={(open) => {
          if (!open) close();
        }}
      >
        <SheetContent
          className="w-full overflow-y-auto rounded-none p-4 pb-[max(1rem,env(safe-area-inset-bottom))] sm:max-w-none [&>button]:min-h-11 [&>button]:min-w-11"
          onCloseAutoFocus={(event) => {
            event.preventDefault();
            returnFocus();
          }}
        >
          <SheetTitle>计划行编辑</SheetTitle>
          <SheetDescription>
            收起后修改保留在本计划中；点击保存计划才写入服务器。
          </SheetDescription>
          {children}
        </SheetContent>
      </Sheet>
    );
  return (
    <section
      ref={desktop}
      aria-label="计划行编辑"
      className="bg-surface space-y-4 rounded-lg border p-4"
    >
      {children}
    </section>
  );
}
