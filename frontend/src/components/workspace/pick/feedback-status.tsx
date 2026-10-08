"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { useAuth } from "@/core/auth/AuthProvider";
import { getFeedbackStatus, refreshFeedback } from "@/core/pick/feedback-api";
import {
  feedbackLabels,
  feedbackErrorText,
  qualityLabels,
  type FeedbackStatus as Status,
} from "@/core/pick/feedback-schema";
import { day } from "@/core/pick/format";

const admissionUnconfirmed =
  "尚未确认刷新任务已开始，后台可能仍在处理；请稍后重新读取状态。";

export function FeedbackStatus() {
  const { user } = useAuth();
  return user ? (
    <StatusForUser key={user.id} />
  ) : (
    <p>请先登录以查看运营反馈。</p>
  );
}
function StatusForUser() {
  const [status, setStatus] = useState<Status>();
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [needsAuth, setNeedsAuth] = useState(false);
  const [busy, setBusy] = useState(false);
  // Keep a just-returned auth failure until status observes that run or a new run.
  const authReceipt = useRef<{
    beforeId: string | null;
    failedId: string | null;
  } | null>(null);
  // A nullable run ID can precede the durable claim; retain the baseline even
  // after observation times out so a manual status read can confirm the new run.
  const admission = useRef<{
    beforeId: string | null;
    runId: string | null;
    observing: boolean;
  } | null>(null);
  const admissionTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const controller = useRef<AbortController | null>(null);
  const readGeneration = useRef(0);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const read = useRef<(manual?: boolean) => Promise<void>>(
    async () => undefined,
  );
  const observeAdmission = useCallback(() => {
    if (!admission.current) return;
    admission.current.observing = true;
    if (admissionTimer.current) clearTimeout(admissionTimer.current);
    admissionTimer.current = setTimeout(() => {
      if (!admission.current || controller.current?.signal.aborted) return;
      admission.current.observing = false;
      if (timer.current) clearTimeout(timer.current);
      setBusy(false);
      setMessage(admissionUnconfirmed);
    }, 60_000);
  }, []);
  useEffect(() => {
    const abort = new AbortController();
    controller.current = abort;
    read.current = async (manual = false) => {
      const generation = ++readGeneration.current;
      if (timer.current) clearTimeout(timer.current);
      try {
        const next = await getFeedbackStatus(abort.signal);
        if (abort.signal.aborted || generation !== readGeneration.current)
          return;
        setStatus(next);
        setError("");
        const active =
          next.enabled &&
          next.configured &&
          !next.lease_expired &&
          next.running?.status === "running";
        const latest = next.running ?? next.last_run;
        if (
          admission.current &&
          latest &&
          (latest.id === admission.current.runId ||
            latest.id !== admission.current.beforeId)
        ) {
          // Status reads can straddle claim commit. A split running frame
          // retains the bounded wait; only a manual recheck can restart it.
          if (active || latest.status !== "running" || next.lease_expired) {
            admission.current = null;
            if (admissionTimer.current) clearTimeout(admissionTimer.current);
            setMessage((previous) =>
              previous === admissionUnconfirmed ? "" : previous,
            );
          } else if (
            manual &&
            !admission.current.observing &&
            next.enabled &&
            next.configured
          ) {
            observeAdmission();
            setMessage(feedbackLabels.refresh_pending);
          }
        }
        if (!next.enabled || !next.configured) {
          admission.current = null;
          if (admissionTimer.current) clearTimeout(admissionTimer.current);
        }
        const awaitingAdmission = admission.current?.observing === true;
        setBusy(active || awaitingAdmission);
        if (!active && !awaitingAdmission)
          setMessage((previous) =>
            previous === feedbackLabels.refresh_pending ? "" : previous,
          );
        if (
          authReceipt.current &&
          latest &&
          (latest.id === authReceipt.current.failedId ||
            latest.id !== authReceipt.current.beforeId)
        ) {
          authReceipt.current = null;
        }
        const authRequired =
          authReceipt.current !== null ||
          latest?.error_code === "auth_required";
        setNeedsAuth(authRequired);
        if (!authRequired)
          setMessage((previous) =>
            previous === feedbackLabels.auth_required ? "" : previous,
          );
        if (active || awaitingAdmission)
          timer.current = setTimeout(() => {
            void read.current();
          }, 2000);
      } catch (e) {
        if (abort.signal.aborted || generation !== readGeneration.current)
          return;
        admission.current = null;
        if (admissionTimer.current) clearTimeout(admissionTimer.current);
        setStatus(undefined);
        setMessage("");
        setNeedsAuth(false);
        setBusy(false);
        setError(e instanceof Error ? e.message : "反馈状态读取失败");
      }
    };
    void read.current();
    return () => {
      abort.abort();
      if (admissionTimer.current) clearTimeout(admissionTimer.current);
      if (timer.current) clearTimeout(timer.current);
    };
  }, [observeAdmission]);
  async function refresh() {
    const signal = controller.current?.signal;
    setBusy(true);
    setError("");
    setMessage("");
    try {
      const result = await refreshFeedback(signal);
      if (signal?.aborted) return;
      setMessage(feedbackLabels[result.status]);
      if (result.status === "refresh_pending") {
        admission.current = {
          beforeId: (status?.running ?? status?.last_run)?.id ?? null,
          runId: result.run_id,
          observing: true,
        };
        observeAdmission();
      } else {
        admission.current = null;
        if (admissionTimer.current) clearTimeout(admissionTimer.current);
      }
      authReceipt.current =
        result.status === "auth_required"
          ? {
              beforeId: (status?.running ?? status?.last_run)?.id ?? null,
              failedId: result.run_id,
            }
          : null;
      setNeedsAuth(result.status === "auth_required");
      await read.current();
    } catch (e) {
      if (signal?.aborted) return;
      setStatus(undefined);
      setNeedsAuth(false);
      setError(e instanceof Error ? e.message : "反馈刷新失败");
      setBusy(false);
    }
  }
  return (
    <section
      className="bg-card space-y-3 rounded-lg border p-5"
      aria-label="飞书运营反馈同步"
    >
      <h2 className="font-semibold">飞书运营反馈</h2>
      <p className="text-muted-foreground text-sm">
        从短剧发行管理读取播放与收益反馈，独立于剧库和榜单同步。
      </p>
      {!status && !error && <p role="status">正在读取反馈状态…</p>}
      {error && (
        <p role="alert" className="text-danger-ink text-sm">
          {error}
        </p>
      )}
      {status && (
        <>
          <p role="status">
            {!status.enabled
              ? "反馈同步未启用"
              : !status.configured
                ? "反馈来源尚未配置"
                : busy
                  ? "反馈刷新中"
                  : status.lease_expired
                    ? "同步任务已过期，需要重新刷新"
                    : status.last_run?.status === "failed"
                      ? "最近反馈刷新失败；旧版本未被覆盖"
                      : status.current
                        ? "已有反馈版本"
                        : "尚无反馈版本"}
          </p>
          {status.current && (
            <div className="space-y-1 text-sm">
              <p>
                版本：{status.current.id} ·{" "}
                {qualityLabels[status.current.source_quality]}
              </p>
              <p>
                读取飞书时间：{day(status.current.scan_completed_at) ?? "未知"}
              </p>
              <p>最近核验：{day(status.last_verified_at) ?? "未知"}</p>
              <p>
                已读取 {status.current.tables.filter((t) => t.complete).length}{" "}
                / {status.current.tables.length}{" "}
                张表；读取完成不代表所有源指标完整。
              </p>
              <details>
                <summary className="min-h-11 cursor-pointer py-3">
                  逐表状态
                </summary>
                <ul>
                  {status.current.tables.map((table) => (
                    <li key={table.table_id}>
                      {table.name} ·{" "}
                      {table.complete ? "读取完整" : "读取不完整"} ·{" "}
                      {qualityLabels[table.source_quality]}
                    </li>
                  ))}
                </ul>
              </details>
            </div>
          )}
          {status.last_run && (
            <p className="text-muted-foreground text-xs">
              最近尝试：{day(status.last_run.started_at) ?? "未知"} · 完成：
              {day(status.last_run.finished_at) ?? "尚未完成"}
            </p>
          )}
          {status.last_run?.error_code && (
            <p className="text-warning-ink">
              {feedbackErrorText(status.last_run.error_code)}
            </p>
          )}
          <Button
            size="sm"
            disabled={busy || !status.enabled || !status.configured}
            onClick={() => void refresh()}
          >
            {busy ? "反馈刷新中…" : "刷新飞书反馈"}
          </Button>
        </>
      )}
      {message && <p role="status">{message}</p>}
      {needsAuth && (
        <a
          className="text-link inline-flex min-h-11 items-center text-sm hover:underline"
          href="/workspace/capabilities?tab=plugins&plugin=lark"
        >
          连接飞书
        </a>
      )}
      {(error || message === admissionUnconfirmed) && (
        <Button variant="outline" onClick={() => void read.current(true)}>
          重新读取状态
        </Button>
      )}
    </section>
  );
}
