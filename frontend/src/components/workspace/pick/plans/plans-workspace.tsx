"use client";
import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useAuth } from "@/core/auth/AuthProvider";
import { listSavedPicks, PickApiError } from "@/core/pick/api";
import { createPlan, getPlan, listPlans } from "@/core/pick/completion-api";
import { type Plan, type PlanCreate } from "@/core/pick/completion-types";

import { PlanEditor } from "./plan-editor";

export function PlansWorkspace({
  planId,
  selectionIds = [],
}: {
  planId?: string;
  selectionIds?: string[];
}) {
  const { user } = useAuth();
  return user ? (
    <OwnedPlans
      key={`${user.id}:${planId ?? "list"}`}
      ownerId={user.id}
      planId={planId}
      selectionIds={selectionIds}
    />
  ) : (
    <p>请先登录以读取个人排期。</p>
  );
}
function OwnedPlans({
  ownerId,
  planId,
  selectionIds,
}: {
  ownerId: string;
  planId?: string;
  selectionIds: string[];
}) {
  const [offset, setOffset] = useState(0);
  const [creating, setCreating] = useState(selectionIds.length > 0);
  const [selected, setSelected] = useState<string[]>(selectionIds);
  const [title, setTitle] = useState("");
  const [timezone, setTimezone] = useState("Asia/Shanghai");
  const [created, setCreated] = useState<Plan | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const pending = useRef<{ key: string; body: PlanCreate } | null>(null);
  const active = useRef(true);
  const request = useRef<AbortController | null>(null);
  useEffect(() => {
    active.current = true;
    return () => {
      active.current = false;
      request.current?.abort();
    };
  }, []);
  const plan = useQuery({
    queryKey: ["pick-plan", ownerId, planId],
    queryFn: ({ signal }) => getPlan(planId!, signal),
    enabled: !!planId,
  });
  const list = useQuery({
    queryKey: ["pick-plans", ownerId, offset],
    queryFn: ({ signal }) => listPlans(offset, signal),
    enabled: !planId,
  });
  const selections = useQuery({
    queryKey: ["pick-selections", ownerId],
    queryFn: ({ signal }) => listSavedPicks(signal),
    enabled: creating && !planId,
  });
  const current = planId ? plan : list;
  const visibleSelected = (selections.data ?? []).filter((row) =>
    selected.includes(row.id),
  );
  if (created) return <PlanEditor key={created.id} initial={created} />;
  if (planId)
    return plan.data ? (
      <PlanEditor key={plan.data.id} initial={plan.data} />
    ) : (
      <div className="p-6">
        <p role={plan.error ? "alert" : "status"}>
          {plan.error ? "排期读取失败或无权访问。" : "正在读取排期…"}
        </p>
        <Button className="min-h-11" onClick={() => void plan.refetch()}>
          重新读取
        </Button>
      </div>
    );
  return (
    <section className="mx-auto w-full max-w-5xl min-w-0 space-y-5 p-4 text-base leading-6 sm:p-6 [&_button]:text-base [&_input]:!text-base [&_textarea]:!text-base">
      <header>
        <h1 className="text-2xl font-bold">排期草稿</h1>
        <p>个人草稿与执行导出记录；导出不代表发布。</p>
      </header>
      {current.isPending && <p role="status">正在读取排期…</p>}
      {current.error && (
        <p role="alert">
          {current.error instanceof PickApiError
            ? current.error.message
            : "排期读取失败，请重试。"}
        </p>
      )}
      <Button className="min-h-11" onClick={() => setCreating(!creating)}>
        {creating ? "收起新建（输入保留）" : "新建排期"}
      </Button>
      {creating && (
        <form
          className="space-y-4 border-y py-4"
          onSubmit={(event) => {
            event.preventDefault();
            if (busy || !title.trim() || !visibleSelected.length) return;
            const key = JSON.stringify({
              title,
              timezone,
              selections: visibleSelected.map((row) => [
                row.id,
                row.source_result_id,
                row.source_item_id,
                row.identity,
              ]),
            });
            if (pending.current?.key !== key)
              pending.current = {
                key,
                body: {
                  request_id: crypto.randomUUID(),
                  title,
                  timezone,
                  rows: visibleSelected.map((row) => ({
                    row_id: crypto.randomUUID(),
                    identity: row.identity,
                    source_result_id: row.source_result_id,
                    source_item_id: row.source_item_id,
                    selection_id: row.id,
                    account: null,
                    channel: null,
                    local_time: null,
                    fold: null,
                    copy_text: "",
                    note: row.note,
                  })),
                },
              };
            setBusy(true);
            setError("");
            request.current = new AbortController();
            void createPlan(pending.current.body, request.current.signal)
              .then((result) => {
                if (active.current) setCreated(result);
              })
              .catch((reason) => {
                if (active.current)
                  setError(
                    reason instanceof PickApiError
                      ? reason.message
                      : "创建结果尚未确认，请重试原操作。",
                  );
              })
              .finally(() => {
                if (active.current) setBusy(false);
              });
          }}
        >
          <fieldset disabled={busy} className="min-w-0 space-y-3">
            <label className="block">
              新计划名称
              <Input
                className="min-h-11"
                required
                value={title}
                onChange={(event) => setTitle(event.target.value)}
              />
            </label>
            <label className="block">
              计划时区（IANA）
              <Input
                className="min-h-11"
                required
                value={timezone}
                onChange={(event) => setTimezone(event.target.value)}
              />
            </label>
            <p>
              从个人选剧清单选择，当前选中 {visibleSelected.length} 部（最多 100
              部）。
            </p>
            {selections.isPending && <p role="status">正在读取个人清单…</p>}
            {selections.error && (
              <p role="alert">清单读取失败，不能创建排期。</p>
            )}
            <label className="flex min-h-11 items-center gap-3">
              <input
                type="checkbox"
                checked={
                  !!selections.data?.length &&
                  visibleSelected.length ===
                    Math.min(selections.data.length, 100)
                }
                onChange={(event) =>
                  setSelected(
                    event.target.checked
                      ? (selections.data ?? [])
                          .slice(0, 100)
                          .map((row) => row.id)
                      : [],
                  )
                }
              />
              选择当前清单前 100 部
            </label>
            <ul>
              {selections.data?.map((row) => (
                <li key={row.id} className="border-t">
                  <label className="flex min-h-11 items-center gap-3 py-2">
                    <input
                      type="checkbox"
                      checked={selected.includes(row.id)}
                      disabled={
                        !selected.includes(row.id) &&
                        visibleSelected.length >= 100
                      }
                      onChange={(event) =>
                        setSelected(
                          event.target.checked
                            ? [...selected, row.id]
                            : selected.filter((id) => id !== row.id),
                        )
                      }
                    />
                    <span className="break-words">
                      {row.snapshot_json.title} · {row.snapshot_json.language}
                    </span>
                  </label>
                </li>
              ))}
            </ul>
          </fieldset>
          {error && <p role="alert">{error}</p>}
          <Button
            className="min-h-11"
            disabled={
              busy ||
              !visibleSelected.length ||
              !!selections.error ||
              !title.trim()
            }
            type="submit"
          >
            {busy ? "正在创建…" : "确认选择并创建草稿"}
          </Button>
        </form>
      )}
      {list.data && (
        <>
          <p>共 {list.data.total} 份排期</p>
          <ul>
            {list.data.items.map((item) => (
              <li key={item.id} className="border-t py-3">
                <Link
                  className="text-link inline-flex min-h-11 items-center underline"
                  href={`/workspace/pick-plans/${encodeURIComponent(item.id)}`}
                >
                  {item.title}
                </Link>
                <p>
                  版本 {item.version} · {item.rows.length} 行 · {item.timezone}{" "}
                  · 更新于 {item.updated_at}
                </p>
              </li>
            ))}
          </ul>
          {list.data.total === 0 && <p>尚无排期草稿。</p>}
          <div className="flex gap-3">
            <Button
              className="min-h-11"
              variant="outline"
              disabled={offset === 0}
              onClick={() => setOffset(Math.max(0, offset - 20))}
            >
              上一页
            </Button>
            <Button
              className="min-h-11"
              variant="outline"
              disabled={list.data.next_offset === null}
              onClick={() => setOffset(list.data.next_offset!)}
            >
              下一页
            </Button>
          </div>
        </>
      )}
    </section>
  );
}
