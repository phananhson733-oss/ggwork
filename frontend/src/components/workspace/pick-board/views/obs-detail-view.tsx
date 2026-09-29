// 工作台新建（TR-24）：一部剧（身份）的趋势雷达详情（设计 6.1；D13）。两个通道并排：各自显示的集合、这部剧的判定行，
// Trends 一侧另列发现段里对上这部剧的词；下面是这对集合里冻结的联动事实。事实照读不重算；可行动性按请求时刻用 obs-link.ts
// 现算，任一时效原因都显示成「时效不符」；「全球同向」「不同市场信号」只作展示，不带动作。每一栏没有行时写成一句话。
import Link from "next/link";

import { LINK_LABEL_TEXT } from "@/core/pick/obs-format";
import {
  ACTIONABILITY_TEXT,
  LINK_RULE_LIMITS,
  linkActionable,
  type Actionability,
} from "@/core/pick/obs-link";
import type {
  ObsDiscovery,
  ObsLink,
  ObsSet,
  ObsState,
} from "@/core/pick/obs-rows";
import { OBS_CHANNEL_LABELS, type ObsChannel } from "@/core/pick/obs-status";
import {
  CONFIRMATION_TEXT,
  LINK_ACTION_TEXT,
  LINK_UNTIMELY,
  NOT_JUDGED_AMBIGUOUS,
  PRESUMED_CORRESPONDENCE,
  TRENDS_FLAG_TEXT,
  TRENDS_ROW_STATE_TEXT,
  WINDOW_TEXT,
  textOf,
} from "@/core/pick/obs-wording";
import type { PickRequest } from "@/core/pick-board/request";
import type { ObsChannelLoad, ObsTabData } from "@/server/pick-board";

import { pickHref, TAB_LABELS } from "../toolbar";

import { GscRowCard, scopeText } from "./obs-gsc-rows";
import {
  at,
  ChannelHead,
  HEADING,
  identityLabel,
  LINK,
  MUTED,
  SECTION,
  SUBHEADING,
} from "./obs-parts";

type DetailData = Extract<ObsTabData, { kind: "detail" }>;

/** ?obs= pins the tab's own channel (queries-obs pinFor). */
const CHANNEL_OF_TAB: Readonly<Record<string, ObsChannel>> = {
  trends: "trends",
  search: "gsc",
};

const EMPTY_ROWS: Readonly<Record<ObsChannel, string>> = {
  trends: "这个 Trends 集合里没有这部剧的判定行（逐剧趋势未上线）。",
  gsc: "这个 GSC 集合里没有这部剧的判定行。",
};

function correspondenceText(row: ObsState): string {
  if (row.correspondence === "confirmed") return "已人工确认对应";
  return row.id_evidence === "strong" ? PRESUMED_CORRESPONDENCE : "未确认对应";
}

function TrendsRow({ row }: { row: ObsState }) {
  const confirmation = row.confirmation
    ? `，${textOf(CONFIRMATION_TEXT, row.confirmation)}`
    : "";
  return (
    <li
      className="border-line border-b py-2 last:border-b-0"
      data-obs-state={row.row_id}
    >
      <div>
        {row.scope} · {textOf(WINDOW_TEXT, row.window_kind)} ·{" "}
        {textOf(TRENDS_ROW_STATE_TEXT, row.state)}
        {confirmation}
      </div>
      <div className={MUTED}>
        {row.tier ? `${row.tier} 档 · ` : ""}
        {correspondenceText(row)}
        {row.ambiguity && row.ambiguity !== "clear"
          ? ` · ${NOT_JUDGED_AMBIGUOUS}`
          : ""}
        {row.flags.length > 0
          ? ` · ${row.flags.map((f) => textOf(TRENDS_FLAG_TEXT, f)).join("、")}`
          : ""}
      </div>
      <div className={MUTED}>
        窗口截至 {at(row.window_end)}，最近一块截至 {at(row.latest_block_end)}
        {row.carried_over ? "；沿用上一次结果" : ""}
        {row.stale ? "（陈旧）" : ""}
      </div>
    </li>
  );
}

function MatchedTerms({
  discoveries,
}: {
  discoveries: readonly ObsDiscovery[];
}) {
  return (
    <>
      <h4 className={SUBHEADING}>发现段里对上这部剧的词</h4>
      {discoveries.length === 0 ? (
        <p className={MUTED}>这个集合的发现段没有对上这部剧的词。</p>
      ) : (
        <ul className="ml-4 list-disc">
          {discoveries.map((d) => (
            <li key={d.discovery_id}>
              {d.normalized_term}（{d.geo}，种子 {d.seed}，首次见到{" "}
              {at(d.first_seen_at)}）
            </li>
          ))}
        </ul>
      )}
    </>
  );
}

function ChannelColumn({
  load,
  rows,
  discoveries,
  req,
  now,
}: {
  load: ObsChannelLoad;
  rows: readonly ObsState[];
  discoveries: readonly ObsDiscovery[];
  req: PickRequest;
  now: Date;
}) {
  const summary =
    load.shown?.summary.channel === "gsc" ? load.shown.summary : null;
  return (
    <div data-obs-column={load.channel}>
      <ChannelHead
        load={load}
        req={req}
        now={now}
        pinnable={CHANNEL_OF_TAB[req.tab] === load.channel}
      />
      {load.shown ? (
        <section className={SECTION}>
          <h3 className={HEADING}>
            {OBS_CHANNEL_LABELS[load.channel]}的判定行
          </h3>
          {rows.length === 0 ? (
            <p className={MUTED}>{EMPTY_ROWS[load.channel]}</p>
          ) : (
            <ul>
              {rows.map((row) =>
                load.channel === "trends" ? (
                  <TrendsRow key={row.row_id} row={row} />
                ) : (
                  <GscRowCard key={row.row_id} row={row} summary={summary} />
                ),
              )}
            </ul>
          )}
          {load.channel === "trends" ? (
            <MatchedTerms discoveries={discoveries} />
          ) : null}
        </section>
      ) : null}
    </div>
  );
}

/** Actionability now, or null when the link-rules version is not registered here (D29: never judged as the newest). */
function actionabilityOf(
  link: ObsLink,
  trends: ObsSet,
  gsc: ObsSet,
  now: Date,
): Actionability | null {
  if (!(link.link_rules_version in LINK_RULE_LIMITS)) return null;
  return linkActionable(
    link,
    trends.published_at,
    gsc.published_at,
    now,
    link.link_rules_version,
  );
}

function verdictText(link: ObsLink, verdict: Actionability | null): string {
  if (verdict === null)
    return `link-rules 版本 ${link.link_rules_version} 本页没有登记，不判定可行动性`;
  if (verdict.actionable)
    return `可行动：${textOf(LINK_ACTION_TEXT, link.label)}（只提示，不自动执行）`;
  if (verdict.reasons.includes("label_not_actionable"))
    return "只作展示，不带动作";
  const why = verdict.reasons.map((r) => ACTIONABILITY_TEXT[r]).join("；");
  return `${LINK_UNTIMELY}：${why}`;
}

function pairText(link: ObsLink): string {
  if (link.country === null) return "身份层（没有共同国家）";
  return `Trends ${link.trends_geo ?? "—"} ↔ GSC ${scopeText(link.country)}`;
}

function LinkItem({
  link,
  trends,
  gsc,
  now,
}: {
  link: ObsLink;
  trends: ObsSet;
  gsc: ObsSet;
  now: Date;
}) {
  return (
    <li
      className="border-line border-b py-2 last:border-b-0"
      data-obs-link={link.label}
    >
      <div>
        <span className="text-ink-1 font-semibold">
          {textOf(LINK_LABEL_TEXT, link.label)}
        </span>{" "}
        <span className={MUTED}>{pairText(link)}</span>
      </div>
      <div>{verdictText(link, actionabilityOf(link, trends, gsc, now))}</div>
      <div className={MUTED}>
        锚点：Trends {at(link.trends_anchor)}，GSC {at(link.gsc_anchor)}，相隔{" "}
        {link.pair_gap_minutes} 分钟
        {link.timely ? "" : `（${LINK_UNTIMELY}）`}
        {link.stale ? "；用到陈旧的 Trends 行" : ""}
      </div>
    </li>
  );
}

function linksEmptyText(
  trends: ObsSet | null,
  gsc: ObsSet | null,
): string | null {
  if (!trends || !gsc) return "两个通道各有一个已发布集合时才有联动事实。";
  if (trends.mode !== gsc.mode)
    return "显示的两个集合一个是影子、一个是生效，联动只在同模式的集合之间写入。";
  return null;
}

function LinkFacts({ data, now }: { data: DetailData; now: Date }) {
  const trends = data.trends.shown;
  const gsc = data.gsc.shown;
  const blocked = linksEmptyText(trends, gsc);
  return (
    <section className={SECTION} data-obs-section="links">
      <h3 className={HEADING}>两个通道的联动</h3>
      {blocked ? (
        <p className={MUTED}>{blocked}</p>
      ) : data.links.length === 0 || !trends || !gsc ? (
        <p className={MUTED}>
          这一对集合里，这部剧没有联动事实。联动事实在较新的集合发布时，与另一通道当时最新的同模式集合配对写入。
        </p>
      ) : (
        <ul>
          {data.links.map((link) => (
            <LinkItem
              key={link.id}
              link={link}
              trends={trends}
              gsc={gsc}
              now={now}
            />
          ))}
        </ul>
      )}
    </section>
  );
}

function titleOf(data: DetailData): string {
  return data.states[0]?.title ?? identityLabel(data.identity);
}

export function ObsDetailView({
  data,
  req,
  now,
}: {
  data: DetailData;
  req: PickRequest;
  now: Date;
}) {
  const rowsOf = (channel: ObsChannel) =>
    data.states.filter((row) => row.channel === channel);
  return (
    <div data-obs-view="detail">
      <p className="mb-2 text-[13px]">
        <Link
          prefetch={false}
          href={pickHref(req, { oid: "" })}
          className={LINK}
        >
          返回「{TAB_LABELS[data.tab]}」
        </Link>
      </p>
      <h2 className="text-ink-1 mb-1 text-[16px] font-semibold">
        {titleOf(data)}
      </h2>
      <p className={`${MUTED} mb-3 text-[12px] break-all`}>
        {identityLabel(data.identity)} · 身份 {data.identity}
      </p>
      <div className="grid gap-4 md:grid-cols-2">
        <ChannelColumn
          load={data.trends}
          rows={rowsOf("trends")}
          discoveries={data.discoveries}
          req={req}
          now={now}
        />
        <ChannelColumn
          load={data.gsc}
          rows={rowsOf("gsc")}
          discoveries={[]}
          req={req}
          now={now}
        />
      </div>
      <LinkFacts data={data} now={now} />
    </div>
  );
}
