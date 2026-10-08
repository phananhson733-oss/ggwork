import {
  type FeedbackItem,
  type FeedbackReply,
  feedbackLabels,
  qualityLabels,
} from "@/core/pick/feedback-schema";
import { day, evidenceDate } from "@/core/pick/format";

const warningLabels: Record<string, string> = {
  master_identity_invalid: "部分主表剧集的选剧台身份格式无效，未用于候选匹配",
  master_identity_unconfirmed: "部分主表剧集的选剧台对应关系尚未确认",
  master_identity_conflict: "部分主表剧集的选剧台对应关系存在重复或冲突",
  master_identity_incompatible: "部分主表对应关系的剧场或语言不一致",
  external_mapping_scope_unknown: "部分外部 ID 的适用范围不明，相关收益未归因",
  external_mapping_unconfirmed: "部分外部 ID 映射尚未确认，相关收益未归因",
  external_mapping_inactive: "部分外部 ID 映射已停用，相关收益未归因",
  external_mapping_conflict:
    "部分外部 ID 映射重复、范围重叠或关联冲突，相关收益未归因",
  external_mapping_target_invalid:
    "部分外部 ID 映射未唯一关联有效且剧场一致的剧集",
  revenue_direct_link_invalid: "部分收益记录未唯一关联有效且剧场一致的剧集",
  revenue_grain_unconfirmed: "部分收益记录未明确为单剧粒度，未分配到剧集",
  revenue_scope_unavailable: "当前筛选条件无法准确归因收益",
  revenue_amount_basis_or_currency_unknown: "部分收益币种或金额口径待确认",
  no_measured_playback: "尚无有效播放观测",
  revenue_lanes_not_additive: "自动、手动与发布记录收益不可直接相加",
  revenue_dates_not_publication_window: "收益统计日期与发布观察窗口不同",
  cumulative_observations_not_fixed_age_comparison: "累计观测未统一发布时长",
  stop_refresh_after_30_days: "源表对超过 30 天的帖子停止刷新",
  source_quality_partial: "源表仅部分覆盖",
  source_quality_unknown: "源表完整性未知",
  invalid_video_url: "部分视频链接无法识别",
  unknown_publication_identity:
    "部分源记录的帖子身份不确定，已排除在去重统计之外",
  unidentified_publications: "部分发布记录缺少帖子标识",
  publication_identity_from_observation_link:
    "部分帖子标识通过飞书关联采集记录确认",
  unresolved_observation_links: "部分采集记录的关联尚未解析",
  unmatched_observations: "部分采集记录未匹配发布记录",
  conflicting_observation_identity: "部分采集记录的帖子身份冲突",
  observation_status_invalid: "部分采集记录状态无效",
  observation_status_partial: "部分采集指标不完整",
  source_metrics_explicitly_missing: "源表标记部分指标缺失",
  ambiguous_post_drama: "部分帖子无法唯一归属到剧集",
  undated_observations: "部分观测缺少采集日期",
  publication_not_confirmed_public: "部分发布记录尚未确认公开发布",
  conflicting_observations: "同一帖子存在冲突观测",
  ambiguous_revenue_drama: "部分收益无法唯一归属到剧集",
  post_rs_currency_and_attribution_unverified:
    "发布记录收益的币种和归因尚待核实",
};
function warningText(value: string) {
  return warningLabels[value] ?? `待核实：${value}`;
}

export function FeedbackSummary({ feedback }: { feedback: FeedbackReply }) {
  return (
    <section
      className="space-y-1 rounded-lg border p-3 text-xs"
      aria-label="运营反馈版本"
    >
      <p className="font-medium">
        运营反馈 · {feedbackLabels[feedback.status]} ·{" "}
        {qualityLabels[feedback.source_quality]}
      </p>
      <p>
        {feedback.freshness === "historical"
          ? "历史候选依据"
          : feedback.freshness === "stale"
            ? "使用旧反馈"
            : feedback.freshness === "fresh_scan"
              ? "本次读取的反馈"
              : "尚无可用反馈"}
      </p>
      {feedback.feedback_version_id && (
        <p>版本：{feedback.feedback_version_id}</p>
      )}
      {feedback.scan_completed_at && (
        <p>读取飞书时间：{day(feedback.scan_completed_at) ?? "未知"}</p>
      )}
      {feedback.last_verified_at && (
        <p>最近核验：{day(feedback.last_verified_at) ?? "未知"}</p>
      )}
      <p>原候选编号与顺序保持不变；使用最新反馈需重新评估。</p>
      {feedback.notice && <p>{feedback.notice}</p>}
      {feedback.warnings.map((w) => (
        <p key={w} className="text-warning-ink">
          {warningText(w)}
        </p>
      ))}
    </section>
  );
}
const metrics: Record<string, string> = {
  views_total: "累计播放",
  likes_total: "点赞",
  comments_total: "评论",
  saves_total: "收藏",
  shares_total: "转发",
  views_mean: "单帖平均播放",
  views_median: "单帖播放中位数",
};
export const lanes = {
  cps_auto: "CPS 自动明细",
  cps_manual: "CPS 手动明细",
  post_rs: "发布记录 RS",
};
export const money = {
  order_amount: "用户订单金额",
  refund: "退款",
  commission: "分成收益",
  advertising: "广告收益",
  brokerage: "经纪收益",
  bonus: "奖金",
  orders: "订单数",
};
export const grains = {
  drama: "单剧",
  post: "帖子",
  account: "账号",
  platform: "平台",
  unknown: "归属未知",
};
export function FeedbackEvidence({ item }: { item: FeedbackItem | undefined }) {
  if (!item)
    return (
      <p className="text-muted-foreground mt-3 text-xs">
        这部剧暂无反馈依据，未验证不代表表现差。
      </p>
    );
  return (
    <section className="mt-3 space-y-1 text-xs" aria-label="运营反馈依据">
      <h3 className="font-medium">
        {item.evidence_kind === "direct"
          ? "本剧实绩"
          : item.evidence_kind === "cohort"
            ? "同类经验（不等于本剧表现）"
            : "暂无可归因反馈"}
      </h3>
      <p>
        样本：{item.coverage.dramas} 部剧 · {item.coverage.posts} 条帖子 ·
        已测播放 {item.coverage.measured_posts} 条 · 缺失{" "}
        {item.coverage.missing_posts} 条
      </p>
      {Object.entries(metrics).map(([key, label]) => (
        <p key={key}>
          {label}：{item.metrics[key] ?? "未知"}
        </p>
      ))}
      <p>
        指标截至：
        {evidenceDate(
          typeof item.metrics.metric_as_of_min === "string"
            ? item.metrics.metric_as_of_min
            : null,
        )}{" "}
        至{" "}
        {evidenceDate(
          typeof item.metrics.metric_as_of_max === "string"
            ? item.metrics.metric_as_of_max
            : null,
        )}
      </p>
      <p>累计表现未统一发布时长，不直接表示题材或地区的因果效果。</p>
      {item.revenue.length ? (
        <>
          <p>各收益来源分别呈现，不相加：</p>
          {item.revenue.map((r, i) => (
            <p key={i}>
              {lanes[r.source_lane]} · {grains[r.grain]} · {money[r.metric]}：
              {r.amount ?? "未知"}
              {r.metric === "orders"
                ? r.amount === null
                  ? ""
                  : " 单"
                : ` ${r.currency}`}{" "}
              · 口径：
              {r.amount_basis ?? "未知"} · {r.records} 条（缺失{" "}
              {r.missing_records} 条）
            </p>
          ))}
        </>
      ) : (
        <p>
          {item.warnings.includes("revenue_scope_unavailable")
            ? "当前筛选无法准确归因收益"
            : "暂无可归因收益"}
          ，不等于零收益。
        </p>
      )}
      {item.warnings.map((w) => (
        <p key={w} className="text-warning-ink">
          {warningText(w)}
        </p>
      ))}
      {item.evidence_refs.length > 0 && (
        <details>
          <summary className="min-h-11 cursor-pointer py-3">
            核对飞书来源（{item.evidence_refs.length}）
          </summary>
          <ul>
            {item.evidence_refs.map((ref, i) => (
              <li key={i}>
                <a
                  className="text-link inline-flex min-h-11 items-center hover:underline"
                  target="_blank"
                  rel="noopener noreferrer"
                  href={`https://gengrowth.feishu.cn/base/OtnsbnRnwaLmnVsJByscTkFMntd?table=${encodeURIComponent(ref.table_id)}&record=${encodeURIComponent(ref.record_id)}`}
                >
                  {ref.source_lane} · {ref.record_id} · 指标日期{" "}
                  {evidenceDate(ref.metric_as_of)}
                </a>
              </li>
            ))}
          </ul>
        </details>
      )}
    </section>
  );
}
