"use client";

import {
  queryModelProjectionSchema,
  type ModelQueryRow,
} from "@/core/pick/completion-types";
import { rowKeyFromIdentity } from "@/core/pick/identity";
import { isMirrorVersion, rowCheckHref } from "@/core/pick/links";
import {
  RANK_LABELS,
  BASIS_LABELS,
  parsePickRequest,
} from "@/core/pick-board/request";

const domains = {
  candidates: "候选资料",
  catalog: "完整剧库",
  rankings: "榜单资料",
  posted: "发布记录",
  rules: "发布规则",
};
const channels = { youtube: "YouTube", tiktok: "TikTok", facebook: "Facebook" };
const ordering = {
  evidence_date: "依据日期",
  evidence: "依据日期",
  listed: "上架日期",
  rank: "来源榜单名次",
  title: "剧名",
  published_at: "发布时间",
};
const metricLabels = {
  rr: "上游近30天大盘",
  d1: "较1天前大盘变化",
  d7: "较7天前大盘变化",
  dp1: "较1天前推广人数变化",
  dp7: "较7天前推广人数变化",
  promoters: "上游推广人数",
  publish: "上线时间",
  bill: "来源账单排名",
  eff: "上游大盘与推广人数比值",
  gsc: "站内搜索曝光",
  clicks: "站内近7天出站点击",
};
const metricUnits = {
  source_cents: "来源美分口径",
  people: "人",
  source_cents_per_promoter: "来源美分/推广人",
  timestamp: "时间",
  rank: "名",
  impressions: "次曝光",
  clicks: "次点击",
};
const metricScopes = {
  upstream_platform_rolling_30d: "上游平台滚动30天大盘",
  change_in_promoters: "上游推广人数变化",
  change_in_platform_rolling_30d: "上游滚动30天大盘较基线变化",
  platform_metric_per_promoter: "上游平台大盘/推广人数",
  publication_date: "来源上线日期",
  upstream_promoters: "上游推广人数",
  source_bill_rank: "来源账单名次",
  site_search_impressions: "站内搜索曝光",
  site_outbound_7d: "站内近7天出站",
};
const knownLabel = (labels: Record<string, string>, key: string) =>
  Object.hasOwn(labels, key) ? labels[key]! : key;
const linkClass = "text-link inline-flex min-h-11 items-center underline";
export function CommonQueryCard({
  result,
  isLoading = false,
}: {
  result: unknown;
  isLoading?: boolean;
}) {
  let value: unknown = result;
  if (typeof value === "string") {
    try {
      value = JSON.parse(value);
    } catch {
      value = null;
    }
  }
  const parsed = queryModelProjectionSchema.safeParse(value);
  if (!parsed.success)
    return (
      <p role={isLoading ? "status" : "alert"} className="my-3 text-base">
        {isLoading
          ? "资料查询处理中…"
          : "资料查询未完成，请查看来源状态或重试。"}
      </p>
    );
  const data = parsed.data,
    request = data.request;
  const conditions = [
    request.query ? `搜索：${request.query}` : null,
    request.source ? `来源：${request.source}` : null,
    request.source_id ? `来源编号：${request.source_id}` : null,
    request.language !== null
      ? `语种：${request.language || "来源未标明"}`
      : null,
    request.theater ? `剧场：${request.theater}` : null,
    request.channel ? `渠道：${channels[request.channel]}` : null,
    request.account ? `账号：${request.account}` : null,
    request.rank ? `榜单：${RANK_LABELS[request.rank]}` : null,
    request.grade ? `评级：${request.grade}` : null,
    request.legacy_week_label
      ? `请求周标签：${request.legacy_week_label}`
      : null,
    request.signal_kind
      ? `依据类型：${knownLabel(BASIS_LABELS, request.signal_kind)}`
      : null,
    request.posted_filter
      ? `发布筛选：${{ no: "当前记录无已发布匹配", yes: "当前记录有已发布匹配", pool: "运营选剧池" }[request.posted_filter]}`
      : null,
    request.posted_state
      ? `记录状态：${{ pub: "已发布", sched: "仅排期", none: "暂无发布及排期", nomatch: "尚无剧目匹配" }[request.posted_state]}`
      : null,
    request.tags.length ? `标签：${request.tags.join("、")}` : null,
    request.exclude_posted ? "排除当前范围已发布" : null,
    request.exclude_selected ? "排除我的已选" : null,
    request.confirmed_eligible_only &&
    request.channel &&
    ["candidates", "catalog"].includes(request.domain)
      ? "仅确认符合资格"
      : null,
    request.hot_only ? "仅热门" : null,
    request.exclude_previous ? "排除前批" : null,
    request.youtube_ok ? "仅 YouTube 可发" : null,
    request.in_use_only ? "仅运营在用剧场" : null,
    request.dated_only ? "仅有依据日期" : null,
    request.with_off ? "包含已下架记录" : null,
    request.published_from ? `发布起日：${request.published_from}` : null,
    request.published_to ? `发布止日：${request.published_to}` : null,
    request.rs_locale ? `ReelShort 语种：${request.rs_locale}` : null,
    request.rs_bucket ? `上线天数：${request.rs_bucket}` : null,
  ].filter((item): item is string => item !== null);
  const version = isMirrorVersion(data.pin.mirror_version)
    ? data.pin.mirror_version
    : null;
  const params = new URLSearchParams({
    tab:
      request.domain === "candidates"
        ? "pick"
        : request.domain === "catalog"
          ? "all"
          : request.domain === "rankings"
            ? "rank"
            : request.domain === "posted"
              ? "posted"
              : "rules",
  });
  if (version !== null) params.set("v", String(version));
  if (request.rank) params.set("rk", request.rank);
  if (data.actual_period?.value)
    params.set(
      data.actual_period.kind === "weekly" ? "week" : "day",
      data.actual_period.value,
    );
  return (
    <section
      className="bg-surface my-3 min-w-0 space-y-3 rounded-lg border p-4 text-base leading-6 [overflow-wrap:anywhere]"
      aria-label="共用资料查询结果"
    >
      <h3 className="text-lg font-semibold">
        {domains[request.domain]}
        {request.domain !== "rules" &&
          ` · ${request.scope === "candidate_pool" ? "候选池范围" : "全库范围"}`}
      </h3>
      <p>{conditions.length ? conditions.join("；") : "本次没有附加筛选"}</p>
      {request.domain !== "rules" && (
        <>
          <p>
            排序：
            {data.effective_sort
              ? Object.hasOwn(metricLabels, data.effective_sort)
                ? metricLabels[data.effective_sort as keyof typeof metricLabels]
                : "来源排序"
              : request.domain === "rankings"
                ? "来源榜单顺序"
                : request.domain === "posted"
                  ? "发布时间"
                  : ordering[request.order]}
            （不代表收益预测）
          </p>
          <p>
            原请求上限 {data.projection.requested_limit} 条 · 本次查询页上限{" "}
            {request.limit} 条
          </p>
        </>
      )}
      {request.domain === "rules" ? (
        <p>
          本次规则资料 {data.projection.available_count} 项 · 卡片展示{" "}
          {data.projection.shown} 项
        </p>
      ) : (
        <>
          <p>
            完整匹配 {data.counts.matched} 条 · 本次查询返回{" "}
            {data.counts.returned} 条 · 卡片展示 {data.projection.shown} 条
          </p>
          {data.counts.matched === 0 && <p>在本次范围与条件下没有匹配记录。</p>}
        </>
      )}
      {data.rank_limit !== null && (
        <p>
          此榜单窗口最多 {data.rank_limit} 条；完整匹配数与展示窗口分别列出。
        </p>
      )}
      {data.query_truncated && <p>查询按页返回，当前不是完整清单。</p>}
      {data.projection.omitted_rows > 0 && (
        <p className="text-warning-ink">
          为控制展示长度，本卡片省略 {data.projection.omitted_rows}{" "}
          项；可打开固定版本资料继续核对。
        </p>
      )}
      {data.projection.signals_omitted > 0 && (
        <p>另有 {data.projection.signals_omitted} 条依据未在卡片中展开。</p>
      )}
      <p>
        实际期次：
        {data.actual_period?.value ??
          (data.actual_period ? "此版本最新可读期次" : "不适用")}
      </p>
      {request.period.value &&
        request.period.value !== data.actual_period?.value && (
          <p className="text-warning-ink">
            请求期次 {request.period.value} 未返回；上方显示的是实际可读期次。
          </p>
        )}
      {data.period_resolution === "ambiguous" && (
        <p className="text-warning-ink">周标签跨年重名，请按周起始日核对。</p>
      )}
      {data.period_resolution === "missing" && (
        <p className="text-warning-ink">
          请求的期次缺失，当前显示可读取的期次。
        </p>
      )}
      <p>
        来源观察时间：{data.source_as_of ?? "未知"} · 镜像同步时间：
        {data.mirror_synced_at ?? "未知"}
      </p>
      <details>
        <summary className="min-h-11 cursor-pointer">版本与依据标识</summary>
        <p>剧库批次：{data.pin.catalog_batch_id}</p>
        <p>知识批次：{data.pin.knowledge_batch_id ?? "无"}</p>
        <p>
          规则版本：{data.pin.rule_version} · 排序版本：{data.order_version}
        </p>
        <p>镜像版本：{version ?? "此来源没有关联镜像"}</p>
        {data.pin.feedback_version_id && (
          <p>反馈版本：{data.pin.feedback_version_id}</p>
        )}
      </details>
      {version !== null && (
        <a
          className={linkClass}
          href={`/workspace/pick-data?${params}`}
          target="_blank"
          rel="noopener noreferrer"
        >
          打开此版本资料（新标签页）
        </a>
      )}
      <ul>
        {data.rows.map((row, index) => (
          <li
            key={`${row.reference}:${index}`}
            className="space-y-1 border-t py-3"
          >
            <ProjectionRow row={row} version={version} />
          </li>
        ))}
      </ul>
      <p className="text-helper">本次仅查询资料，未执行保存或发布。</p>
    </section>
  );
}

function ProjectionRow({
  row,
  version,
}: {
  row: ModelQueryRow;
  version: number | null;
}) {
  const key =
    row.kind === "catalog_record"
      ? row.row_key
      : row.kind === "drama"
        ? rowKeyFromIdentity(row.identity)
        : row.kind === "bill" && row.canonical_id
          ? `reelshort-${row.canonical_id}`
          : null;
  const detailHref =
    version !== null && key
      ? rowCheckHref(key, version)
      : version !== null &&
          row.kind === "posted" &&
          parsePickRequest({ tab: "posted", sd: row.sd }).sd
        ? `/workspace/pick-data?${new URLSearchParams({ tab: "posted", sd: row.sd, v: String(version) })}`
        : null;
  return (
    <>
      {row.kind === "rule" ? (
        <>
          <strong>
            {row.name}
            {row.name_truncated ? "（名称已截断）" : ""}
          </strong>
          <p>
            来源剧场：{row.platform} · YouTube：
            {row.youtube_rule === "ok"
              ? "允许"
              : row.youtube_rule === "no"
                ? "禁止"
                : row.youtube_rule === "only"
                  ? "仅剧单记录"
                  : row.youtube_rule === "warn"
                    ? "有额外限制，需核对"
                    : "待核对"}
          </p>
        </>
      ) : (
        <strong>
          {row.title}
          {"title_truncated" in row && row.title_truncated
            ? "（标题已截断）"
            : ""}
        </strong>
      )}
      {row.kind === "drama" && (
        <>
          <p>
            {row.theater} · {row.language || "来源未标明语种"} · 来源：
            {row.source} / {row.source_id}
          </p>
          <p>
            {row.availability === "active"
              ? "来源标记在架"
              : row.availability === "delisted"
                ? "已下架"
                : "上下架状态未知"}{" "}
            ·{" "}
            {row.posted_status === "posted"
              ? "当前范围有已发布记录"
              : row.posted_status === "not_posted" && row.posted_scope_complete
                ? "当前完整范围无已发布记录"
                : "发布情况未知"}
          </p>
          {Object.entries(row.channel_rules).map(([channel, rule]) => (
            <p key={channel}>
              {channels[channel as keyof typeof channels]}：
              {rule === "allowed"
                ? "允许"
                : rule === "denied"
                  ? "禁止"
                  : "资格待核对"}
            </p>
          ))}
          <details>
            <summary className="min-h-11 cursor-pointer">
              本条依据（展示 {row.signals.length} / {row.signal_count} 条）
            </summary>
            <ul>
              {row.signals.map((signal) => (
                <li key={signal.reference}>
                  {knownLabel(BASIS_LABELS, signal.kind)} · 观测日{" "}
                  {signal.observed_at ?? "未知"} · 数值{" "}
                  {signal.value ?? "未提供"} · 名次 {signal.rank ?? "未提供"} ·
                  等级 {signal.grade || "未提供"}
                </li>
              ))}
            </ul>
            {row.signals_truncated && (
              <p>部分依据已省略，请到固定版本资料核对。</p>
            )}
          </details>
        </>
      )}
      {row.kind === "catalog_record" && (
        <>
          <p>
            {row.theater} · {row.language || "来源未标明语种"}
          </p>
          <p>
            来源记录：{row.row_key} · {row.source_ref}
          </p>
          <p>
            {row.availability === "delisted" ? "已下架" : "上下架状态未知"} ·
            候选资格待核对
          </p>
          <p>上架日期：{row.listed_on ?? "未提供"}</p>
        </>
      )}
      {row.kind === "posted" && (
        <>
          <p>
            来源编号：{row.sd} · 最近发布 {row.last_post_on ?? "未提供"}
          </p>
          <p>
            来源记录累计已发布 {row.post_count} 条 · 累计已排期{" "}
            {row.sched_count} 条{row.archived ? " · 记录已归档" : ""}
          </p>
          <p>累计数不等于当前账号或日期窗口内的条数。</p>
          <p>
            账号：{row.accounts.join("、") || "未提供"}
            {row.accounts_truncated
              ? `（仅展示 ${row.accounts.length} / ${row.account_count} 个）`
              : ""}
          </p>
        </>
      )}
      {row.kind === "bill" && (
        <>
          <p>
            来源编号：{row.book_id} · 账单日：{row.bill_date} · 推广类型：
            {row.promotion_type || "未提供"} · 订单笔数：
            {row.order_cnt}
          </p>
          <p>
            {row.canonical_id
              ? `已对应剧目 ${row.canonical_id}`
              : "未对应剧目，不推断归因"}
          </p>
        </>
      )}
      {(row.kind === "drama" || row.kind === "catalog_record") &&
        row.rank_metric && <RankMetric metric={row.rank_metric} />}
      {detailHref && (
        <a
          className={linkClass}
          href={detailHref}
          target="_blank"
          rel="noopener noreferrer"
        >
          查看这条来源（新标签页）
        </a>
      )}
    </>
  );
}

function RankMetric({
  metric,
}: {
  metric: NonNullable<Extract<ModelQueryRow, { kind: "drama" }>["rank_metric"]>;
}) {
  const platformMetric = [
    "rr",
    "d1",
    "d7",
    "dp1",
    "dp7",
    "promoters",
    "eff",
  ].includes(metric.key);
  const available = !platformMetric || metric.verified === true;
  const value = available ? metric.value : null;
  return (
    <section
      className="my-2 space-y-1 rounded border p-3"
      aria-label="本次排序依据"
    >
      <h4>{metricLabels[metric.key]}</h4>
      <p>
        口径：{metricScopes[metric.scope]} · 单位：{metricUnits[metric.unit]}
      </p>
      <p>数值：{value ?? "未提供/未核实"}</p>
      {metric.verified !== true && (
        <p>校验状态：{metric.verified === false ? "未通过" : "未提供"}</p>
      )}
      {available && metric.current !== null && metric.baseline !== null && (
        <p>
          当前值 {metric.current} · {metric.comparison_days} 天前基线{" "}
          {metric.baseline}
        </p>
      )}
      {available && metric.key === "eff" && metric.current !== null && (
        <p>
          来源分子 {metric.current} / 推广人数 {metric.denominator ?? "未提供"}
          ；未计算或确认精确比值。
        </p>
      )}
      <p>
        观测时间：{metric.observed_at ?? "未知"}
        {metric.baseline_at ? ` · 基线时间：${metric.baseline_at}` : ""}
      </p>
      {platformMetric && <p>上游大盘口径，不表示我们的收入或分成。</p>}
    </section>
  );
}
