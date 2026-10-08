"use client";
import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useAuth } from "@/core/auth/AuthProvider";
import { PickApiError } from "@/core/pick/api";
import {
  getPlan,
  linkPlanPost,
  listPlans,
  listReviewPosts,
} from "@/core/pick/completion-api";
import {
  type PlanLink,
  type PlanLinkCommand,
  type ReviewPost,
  type ReviewQuery,
} from "@/core/pick/completion-types";
import { replayHref } from "@/core/pick/links";

import { grains, lanes, money } from "./feedback-evidence";
import { PostFollowUpQuery } from "./review-query";
const control =
  "min-h-11 w-full rounded-md border border-line bg-surface p-2 text-base";
function Evidence({ refs }: { refs: string[] }) {
  return (
    <ul>
      {refs.map((ref) => {
        let href: string | null = null;
        try {
          const url = new URL(ref);
          if (["https:", "http:"].includes(url.protocol)) href = url.href;
        } catch {}
        return (
          <li key={ref} className="break-all">
            {href ? (
              <a
                href={href}
                target="_blank"
                rel="noreferrer"
                className="text-link inline-flex min-h-11 items-center underline"
              >
                来源证据（新标签页）：{ref}
              </a>
            ) : (
              ref
            )}
          </li>
        );
      })}
    </ul>
  );
}
export function PostReview() {
  const { user } = useAuth();
  return user ? (
    <OwnedPostReview key={user.id} ownerId={user.id} />
  ) : (
    <p>请先登录以读取个人发布复盘。</p>
  );
}
export function OwnedPostReview({ ownerId }: { ownerId: string }) {
  const [filters, setFilters] = useState({
    account_id: "",
    channel: "",
    language: "",
    published_from: "",
    published_to: "",
  });
  const [query, setQuery] = useState<Partial<ReviewQuery>>({});
  const [selected, setSelected] = useState<ReviewPost | null>(null);
  const [queryPost, setQueryPost] = useState<ReviewPost | null>(null);
  const posts = useQuery({
    queryKey: ["pick-review", ownerId, query],
    queryFn: ({ signal }) => listReviewPosts(query, signal),
  });
  const data = posts.data;
  return (
    <section className="mx-auto w-full max-w-6xl min-w-0 space-y-5 p-4 text-base leading-6 sm:p-6 [&_button]:text-base [&_input]:!text-base [&_textarea]:!text-base">
      <header>
        <h1 className="text-2xl font-bold">发布复盘</h1>
        <p>读取真实发布记录及观察窗口。计划导出不代表发布。</p>
      </header>
      <form
        className="grid gap-3 sm:grid-cols-2"
        onSubmit={(event) => {
          event.preventDefault();
          setQuery({
            account_id: filters.account_id || null,
            channel: (filters.channel || null) as ReviewQuery["channel"],
            language: filters.language || null,
            published_from: filters.published_from || null,
            published_to: filters.published_to || null,
            offset: 0,
          });
          setSelected(null);
        }}
      >
        <label>
          发布账号
          <Input
            className="min-h-11"
            value={filters.account_id}
            onChange={(event) =>
              setFilters({ ...filters, account_id: event.target.value })
            }
          />
        </label>
        <label>
          平台
          <select
            className={control}
            value={filters.channel}
            onChange={(event) =>
              setFilters({ ...filters, channel: event.target.value })
            }
          >
            <option value="">全部平台</option>
            <option value="youtube">YouTube</option>
            <option value="tiktok">TikTok</option>
            <option value="facebook">Facebook</option>
          </select>
        </label>
        <label>
          语种
          <Input
            className="min-h-11"
            value={filters.language}
            onChange={(event) =>
              setFilters({ ...filters, language: event.target.value })
            }
          />
        </label>
        <label>
          发布开始日期
          <Input
            className="min-h-11"
            type="date"
            value={filters.published_from}
            onChange={(event) =>
              setFilters({ ...filters, published_from: event.target.value })
            }
          />
        </label>
        <label>
          发布结束日期
          <Input
            className="min-h-11"
            type="date"
            value={filters.published_to}
            onChange={(event) =>
              setFilters({ ...filters, published_to: event.target.value })
            }
          />
        </label>
        <Button className="min-h-11" type="submit">
          读取记录
        </Button>
      </form>
      {posts.isPending && <p role="status">正在读取发布记录…</p>}
      {posts.error && (
        <div role="alert">
          <p>
            {posts.error instanceof PickApiError
              ? posts.error.message
              : "发布记录读取失败；没有资料不表示没有发布。"}
          </p>
          <Button
            className="min-h-11"
            variant="outline"
            onClick={() => void posts.refetch()}
          >
            重试读取
          </Button>
        </div>
      )}
      {data && (
        <>
          <p>
            反馈版本：{data.feedback_version_id ?? "尚未取得"} · 来源读取时间：
            {data.scan_completed_at ?? "未知"}
          </p>
          {data.status !== "ok" ? (
            <p role="status">
              {data.status === "disabled"
                ? "该数据源尚未启用，请联系管理员。"
                : data.status === "auth_required"
                  ? "当前账号没有可用的来源授权，请联系管理员。"
                  : "该数据源暂时不可用，请稍后重试。"}
            </p>
          ) : (
            <>
              <p>
                当前范围共 {data.total ?? "未知数量的"}{" "}
                条真实发布记录；指标按来源与币种分别展示。
              </p>
              {data.total === 0 && <p>当前范围没有可读取的发布记录。</p>}
              {data.warnings.map((warning, index) => (
                <p key={index} className="text-warning-ink">
                  {warning}
                </p>
              ))}
              <ul>
                {data.items.map((post) => (
                  <li key={post.post_key} className="space-y-2 border-t py-4">
                    <h2 className="text-lg font-semibold break-words">
                      {post.title || "源记录未提供剧名"}
                    </h2>
                    <p>
                      {post.account_id ?? "账号未知"} ·{" "}
                      {post.channel ?? "平台未知"} ·{" "}
                      {post.language ?? "语种未知"}
                    </p>
                    <p>
                      发布：{post.published_at ?? "时间未知"} · 观测：
                      {post.observed_at ?? "未更新"}
                    </p>
                    <p>
                      已观察 {post.observation_days ?? "未知"} 天 / 目标{" "}
                      {post.requested_observation_days} 天
                      {post.window_complete ? "（窗口完整）" : "（窗口不足）"}
                    </p>
                    <div className="flex flex-wrap gap-4">
                      <span>播放：{post.views ?? "未提供/未更新"}</span>
                      <span>点赞：{post.likes ?? "未提供/未更新"}</span>
                      <span>评论：{post.comments ?? "未提供/未更新"}</span>
                    </div>
                    <p>
                      {post.link
                        ? post.link.status === "confirmed"
                          ? `已确认关联 · ${post.link.method === "manual" ? "人工核对" : "唯一外部 ID 核对"}`
                          : post.link.status === "needs_review"
                            ? "关联需要重新核对"
                            : "关联存在冲突"
                        : "未关联计划"}
                    </p>
                    {!post.identity && <p>剧目身份未归因</p>}
                    {post.revenue.length === 0 ? (
                      <p>收益未提供/未更新</p>
                    ) : (
                      post.revenue.map((revenue, index) => (
                        <p key={`${revenue.record_id}:${index}`}>
                          {lanes[revenue.source_lane]} · {grains[revenue.grain]}{" "}
                          · {money[revenue.metric]}：
                          {revenue.amount ?? "未提供"} {revenue.currency} ·{" "}
                          {revenue.amount_basis ?? "口径未知"} ·{" "}
                          {revenue.metric_on ?? "日期未知"} ·{" "}
                          {revenue.attribution === "confirmed"
                            ? "已归因"
                            : "未归因"}
                        </p>
                      ))
                    )}
                    <details>
                      <summary className="min-h-11 cursor-pointer">
                        来源与关联依据
                      </summary>
                      <p className="break-all">帖子标识：{post.post_key}</p>
                      <Evidence refs={post.evidence_refs} />
                      {post.link && (
                        <>
                          <Link
                            href={`/workspace/pick-plans/${encodeURIComponent(post.link.plan_id)}`}
                            className="text-link inline-flex min-h-11 items-center underline"
                          >
                            打开计划当前版本（原关联版本{" "}
                            {post.link.plan_version}）
                          </Link>
                          <Evidence refs={post.link.evidence_refs} />
                        </>
                      )}
                    </details>
                    <Button
                      className="min-h-11"
                      variant="outline"
                      disabled={!data.feedback_version_id}
                      onClick={() => setSelected(post)}
                    >
                      人工关联计划行
                    </Button>
                    <Button
                      className="ml-3 min-h-11"
                      variant="outline"
                      disabled={!data.feedback_version_id}
                      onClick={() => setQueryPost(post)}
                    >
                      继续选剧（发起新查询）
                    </Button>
                  </li>
                ))}
              </ul>
              <div className="flex gap-3">
                <Button
                  className="min-h-11"
                  variant="outline"
                  disabled={!query.offset}
                  onClick={() => {
                    setSelected(null);
                    setQuery({
                      ...query,
                      feedback_version_id: data.feedback_version_id,
                      offset: Math.max(0, (query.offset ?? 0) - 20),
                    });
                  }}
                >
                  上一页
                </Button>
                <Button
                  className="min-h-11"
                  variant="outline"
                  disabled={data.next_offset === null}
                  onClick={() => {
                    setSelected(null);
                    setQuery({
                      ...query,
                      feedback_version_id: data.feedback_version_id,
                      offset: data.next_offset!,
                    });
                  }}
                >
                  下一页
                </Button>
              </div>
            </>
          )}
          {queryPost && data.feedback_version_id && (
            <PostFollowUpQuery
              key={`${queryPost.post_key}:${data.feedback_version_id}`}
              ownerId={ownerId}
              post={queryPost}
              feedbackVersion={data.feedback_version_id}
            />
          )}
          {selected && data.feedback_version_id && (
            <ManualLink
              key={`${selected.post_key}:${data.feedback_version_id}`}
              ownerId={ownerId}
              post={selected}
              feedbackVersion={data.feedback_version_id}
              close={() => setSelected(null)}
              onLinked={() => void posts.refetch()}
            />
          )}
        </>
      )}
    </section>
  );
}
function ManualLink({
  ownerId,
  post,
  feedbackVersion,
  close,
  onLinked,
}: {
  ownerId: string;
  post: ReviewPost;
  feedbackVersion: string;
  close: () => void;
  onLinked: () => void;
}) {
  const [offset, setOffset] = useState(0);
  const [planId, setPlanId] = useState("");
  const [rowId, setRowId] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [receipt, setReceipt] = useState<PlanLink | null>(null);
  const command = useRef<{ key: string; body: PlanLinkCommand } | null>(null);
  const active = useRef(true);
  const abort = useRef<AbortController | null>(null);
  useEffect(() => {
    active.current = true;
    return () => {
      active.current = false;
      abort.current?.abort();
    };
  }, []);
  const plans = useQuery({
    queryKey: ["pick-plans", ownerId, offset],
    queryFn: ({ signal }) => listPlans(offset, signal),
  });
  const plan = useQuery({
    queryKey: ["pick-plan", ownerId, planId],
    queryFn: ({ signal }) => getPlan(planId, signal),
    enabled: !!planId,
  });
  const row = plan.data?.rows.find((item) => item.row_id === rowId);
  return (
    <section className="space-y-3 border p-4" aria-label="人工关联核对">
      <h2 className="text-lg">核对双方证据</h2>
      <p>
        真实帖子：{post.title} · {post.account_id ?? "账号未知"} ·{" "}
        {post.channel ?? "平台未知"} · {post.published_at ?? "时间未知"}
      </p>
      <Evidence refs={post.evidence_refs} />
      <label className="block">
        选择个人计划
        <select
          className={control}
          value={planId}
          disabled={busy}
          onChange={(event) => {
            setPlanId(event.target.value);
            setRowId("");
            setConfirmed(false);
            setReceipt(null);
          }}
        >
          <option value="">请明确选择</option>
          {plans.data?.items.map((item) => (
            <option key={item.id} value={item.id}>
              {item.title}（版本 {item.version}）
            </option>
          ))}
        </select>
      </label>
      {plans.error && <p role="alert">个人计划读取失败。</p>}
      <div className="flex gap-3">
        <Button
          className="min-h-11"
          variant="outline"
          disabled={busy || offset === 0}
          onClick={() => setOffset(Math.max(0, offset - 20))}
        >
          前一页计划
        </Button>
        <Button
          className="min-h-11"
          variant="outline"
          disabled={busy || plans.data?.next_offset == null}
          onClick={() => setOffset(plans.data!.next_offset!)}
        >
          后一页计划
        </Button>
      </div>
      <label className="block">
        选择计划行
        <select
          className={control}
          value={rowId}
          disabled={busy || !plan.data}
          onChange={(event) => {
            setRowId(event.target.value);
            setConfirmed(false);
            setReceipt(null);
          }}
        >
          <option value="">请明确选择</option>
          {plan.data?.rows.map((item) => (
            <option key={item.row_id} value={item.row_id}>
              {item.title} · {item.account ?? "账号未填"} ·{" "}
              {item.local_time ?? "时间未填"}
            </option>
          ))}
        </select>
      </label>
      {plan.error && <p role="alert">计划读取失败或无权访问。</p>}
      {row && (
        <div>
          <p>
            计划行：{row.title} · {row.identity} · {row.account ?? "账号未填"} ·{" "}
            {row.channel ?? "渠道未填"}
          </p>
          <p>
            {row.local_time ?? "时间未填"} · {plan.data?.timezone} · 原始规则{" "}
            {row.source_pin.rule_version}
          </p>
          <Link
            className="text-link inline-flex min-h-11 items-center underline"
            target="_blank"
            href={replayHref(
              row.source_result_id,
              row.source_pin.mirror_version,
            )}
          >
            查看原候选依据（新标签页）
          </Link>
        </div>
      )}
      <label className="flex min-h-11 items-center gap-3">
        <input
          type="checkbox"
          checked={confirmed}
          disabled={!row || busy}
          onChange={(event) => setConfirmed(event.target.checked)}
        />
        我已核对两侧身份、账号、时间与来源证据，确认关联
      </label>
      {error && <p role="alert">{error}</p>}
      {receipt && (
        <p role="status">
          {receipt.status === "confirmed"
            ? "关联已确认；这不执行发帖。"
            : "关联仍待核对。"}
        </p>
      )}
      <div className="flex flex-wrap gap-3">
        <Button
          className="min-h-11"
          disabled={busy || !confirmed || !row || !!receipt || !!plan.error}
          onClick={() => {
            if (!plan.data || !row) return;
            const body = {
              plan_id: plan.data.id,
              row_id: row.row_id,
              expected_plan_version: plan.data.version,
              feedback_version_id: feedbackVersion,
              post_key: post.post_key,
              confirmation: "manual" as const,
            };
            const key = JSON.stringify(body);
            if (command.current?.key !== key)
              command.current = {
                key,
                body: { ...body, request_id: crypto.randomUUID() },
              };
            setBusy(true);
            setError("");
            abort.current = new AbortController();
            void linkPlanPost(command.current.body, abort.current.signal)
              .then((result) => {
                if (active.current) {
                  setReceipt(result);
                  onLinked();
                }
              })
              .catch((reason) => {
                if (active.current) {
                  setError(
                    reason instanceof PickApiError
                      ? reason.message
                      : "关联结果未知，请保留双方选择并重试原操作。",
                  );
                  if (
                    reason instanceof PickApiError &&
                    [
                      "version_conflict",
                      "link_conflict",
                      "version_gone",
                    ].includes(reason.code ?? "")
                  ) {
                    setConfirmed(false);
                    void plan.refetch();
                    onLinked();
                  }
                }
              })
              .finally(() => {
                if (active.current) setBusy(false);
              });
          }}
        >
          确认双方证据并关联
        </Button>
        <Button
          className="min-h-11"
          variant="outline"
          disabled={busy}
          onClick={close}
        >
          关闭核对
        </Button>
      </div>
    </section>
  );
}
