"use client";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { PickApiError } from "@/core/pick/api";
import { queryPick } from "@/core/pick/completion-api";
import { type QueryPin, type ReviewPost } from "@/core/pick/completion-types";

/** A new current query; the post and its feedback version remain historical evidence. */
export function PostFollowUpQuery({
  ownerId,
  post,
  feedbackVersion,
}: {
  ownerId: string;
  post: ReviewPost;
  feedbackVersion: string;
}) {
  const [offset, setOffset] = useState(0);
  const [pin, setPin] = useState<QueryPin | null>(null);
  const result = useQuery({
    queryKey: [
      "pick-review-new-query",
      ownerId,
      post.post_key,
      feedbackVersion,
      offset,
      pin,
    ],
    queryFn: ({ signal }) =>
      queryPick(
        {
          domain: "candidates",
          scope: "candidate_pool",
          language: post.language,
          channel: post.channel,
          account: post.account_id,
          exclude_selected: true,
          confirmed_eligible_only: true,
          pin,
          offset,
          limit: 20,
        },
        signal,
      ),
    retry: false,
  });
  return (
    <section className="space-y-3 border p-4" aria-label="复盘后的新查询">
      <h2 className="text-lg">按这条发布记录的账号、平台和语种重新查询</h2>
      <p>
        原反馈版本 {feedbackVersion}{" "}
        保留；新查询使用当前来源，排序不表示收益预测。
      </p>
      {result.isPending && <p role="status">正在查询新候选…</p>}
      {result.error && (
        <p role="alert">
          {result.error instanceof PickApiError
            ? result.error.message
            : "新查询未完成，请重试。"}
        </p>
      )}
      {result.data && (
        <>
          <p>
            范围：候选池 · 完整匹配 {result.data.counts.matched} 部 · 当前页{" "}
            {result.data.counts.returned} 部
          </p>
          <p>
            新剧库 {result.data.pin.catalog_batch_id} · 排序{" "}
            {result.data.order_version} · 源时间{" "}
            {result.data.source_as_of ?? "未知"} · 镜像同步{" "}
            {result.data.mirror_synced_at ?? "未知"}
          </p>
          <ul>
            {result.data.rows.map((row) => (
              <li className="border-t py-3" key={row.identity}>
                <strong>{row.drama.title}</strong>
                <p>
                  {row.drama.theater} · {row.drama.language} ·{" "}
                  {row.drama.source}/{row.drama.source_id}
                </p>
                <p>
                  {row.posted_status === "posted"
                    ? "当前范围已有发布"
                    : row.posted_status === "not_posted"
                      ? "当前完整范围未发布"
                      : "发布情况未知"}
                </p>
              </li>
            ))}
          </ul>
          {result.data.warnings.map((warning, index) => (
            <p key={index}>{warning}</p>
          ))}
          <div className="flex gap-3">
            <Button
              className="min-h-11"
              variant="outline"
              disabled={offset === 0}
              onClick={() => {
                setPin(result.data.pin);
                setOffset(Math.max(0, offset - 20));
              }}
            >
              上一页新候选
            </Button>
            <Button
              className="min-h-11"
              variant="outline"
              disabled={result.data.next_offset === null}
              onClick={() => {
                setPin(result.data.pin);
                setOffset(result.data.next_offset!);
              }}
            >
              下一页新候选
            </Button>
          </div>
        </>
      )}
      <Button
        className="min-h-11"
        variant="outline"
        onClick={() => void result.refetch()}
      >
        重新读取本次查询
      </Button>
    </section>
  );
}
