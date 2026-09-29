// 工作台新建（TR-24）：资料页的「Google 趋势」tab。G2（2026-09-29）定为 b_only：只写「逐剧趋势未上线，发现队列已启用」
// 的说明、发现队列与未覆盖清单，不画逐剧曲线，也不出逐剧的上升判定。发现段按去向分开列：池内唯一匹配进队列，池外命中
// 只展示，两者不合并；A 档只在集合里真有时才列。计数都取自集合摘要与视图本身，没有集合、没有行时写成一句话，不写成 0。
import {
  DISCOVERY_ROUTES,
  type DiscoveryRoute,
  type ObsDiscovery,
  type ObsSet,
  type ObsTrendsSummary,
} from "@/core/pick/obs-rows";
import { obsBannerText } from "@/core/pick/obs-status";
import {
  DATA_SOURCE_TRENDS,
  DISCOVERY_MATCH_TEXT,
  DISCOVERY_ROUTE_TEXT,
  NOT_JUDGED_AMBIGUOUS,
  TRENDS_B_ONLY,
  UNCOVERED_REASON_TEXT,
  textOf,
} from "@/core/pick/obs-wording";
import type { PickRequest } from "@/core/pick-board/request";
import type { ObsDiscoveryPage, ObsTabData } from "@/server/pick-board";

import {
  at,
  ChannelHead,
  HEADING,
  identityLabel,
  IdentityLink,
  MUTED,
  SECTION,
  SUBHEADING,
  TABLE,
  TD,
  TH,
} from "./obs-parts";

type TrendsData = Extract<ObsTabData, { kind: "trends" }>;

const EMPTY_ROUTE: Readonly<Record<DiscoveryRoute, string | null>> = {
  queue: "这个集合的发现段没有进队列的词。",
  display_only: "这个集合没有池外命中。",
  a_tier: null,
};

function BOnlyNote() {
  return (
    <section className={SECTION} data-obs-b-only="true">
      <p className="text-ink-1 font-semibold">{TRENDS_B_ONLY}</p>
      <p className={MUTED}>
        G2（2026-09-29）定为只走发现段（b_only）：本页不画逐剧曲线，也不出逐剧的上升判定；下面是发现队列与未覆盖清单。
      </p>
    </section>
  );
}

function share(value: number | null): string {
  return value === null ? "未计算" : `${Math.round(value * 1000) / 10}%`;
}

function SummaryLines({ summary }: { summary: ObsTrendsSummary }) {
  const codes = summary.status_codes;
  return (
    <ul className="list-inside list-disc">
      <li>
        计划查询 {summary.planned_units} 个单元，取到 {summary.fetched_units}{" "}
        个；熔断 {summary.breaker_events} 次；全零率{" "}
        {share(summary.all_zero_rate)}。
      </li>
      <li>
        {NOT_JUDGED_AMBIGUOUS}：{summary.ambiguous_undecided} 行。
      </li>
      <li>
        沿用上一次结果的行 {summary.carried_over} 行，其中陈旧 {summary.stale}{" "}
        行。
      </li>
      <li>
        集合状态码：
        {codes.length === 0 ? "没有" : codes.map(obsBannerText).join("；")}
      </li>
    </ul>
  );
}

function DiscoveryRow({ d, req }: { d: ObsDiscovery; req: PickRequest }) {
  return (
    <tr data-obs-discovery={d.discovery_id}>
      <td className={TD}>
        {d.normalized_term}
        {d.breakout ? <span className={MUTED}>（Breakout）</span> : null}
        <div className={MUTED}>原词：{d.term}</div>
      </td>
      <td className={TD}>{d.geo}</td>
      <td className={TD}>
        {d.seed}（{d.property === "youtube" ? "YouTube" : "网页"}）
      </td>
      <td className={TD}>
        {d.matched_identity ? (
          <IdentityLink
            req={req}
            identity={d.matched_identity}
            setId={d.set_id}
          >
            {identityLabel(d.matched_identity)}
          </IdentityLink>
        ) : (
          textOf(DISCOVERY_MATCH_TEXT, d.match_status)
        )}
      </td>
      <td className={TD}>{at(d.first_seen_at)}</td>
    </tr>
  );
}

function RouteTable({
  route,
  rows,
  count,
  req,
}: {
  route: DiscoveryRoute;
  rows: readonly ObsDiscovery[];
  count: number | undefined;
  req: PickRequest;
}) {
  const empty = EMPTY_ROUTE[route];
  if (rows.length === 0 && count === undefined)
    return empty === null ? null : (
      <div data-obs-route={route}>
        <h4 className={SUBHEADING}>{DISCOVERY_ROUTE_TEXT[route]}</h4>
        <p className={MUTED}>{empty}</p>
      </div>
    );
  return (
    <div data-obs-route={route}>
      <h4 className={SUBHEADING}>
        {DISCOVERY_ROUTE_TEXT[route]}：{count ?? rows.length} 条
      </h4>
      <table className={TABLE}>
        <thead>
          <tr>
            <th scope="col" className={TH}>
              词（规范化）
            </th>
            <th scope="col" className={TH}>
              geo
            </th>
            <th scope="col" className={TH}>
              种子
            </th>
            <th scope="col" className={TH}>
              对应的剧
            </th>
            <th scope="col" className={TH}>
              首次见到
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.map((d) => (
            <DiscoveryRow key={d.discovery_id} d={d} req={req} />
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Discoveries({
  page,
  req,
}: {
  page: ObsDiscoveryPage;
  req: PickRequest;
}) {
  return (
    <section className={SECTION} data-obs-section="discoveries">
      <h3 className={HEADING}>发现段</h3>
      {DISCOVERY_ROUTES.map((route) => (
        <RouteTable
          key={route}
          route={route}
          rows={page.rows.filter((d) => d.route === route)}
          count={page.counts[route]}
          req={req}
        />
      ))}
      {page.truncated ? (
        <p className={MUTED}>
          只列出前 {page.rows.length} 条；各去向的条数是集合里的全数。
        </p>
      ) : null}
    </section>
  );
}

function Uncovered({
  setId,
  summary,
  req,
}: {
  setId: string;
  summary: ObsTrendsSummary;
  req: PickRequest;
}) {
  const units = summary.uncovered_units;
  return (
    <section className={SECTION} data-obs-section="uncovered">
      <h3 className={HEADING}>
        {units.length === 0 ? "未覆盖的单元" : `未覆盖 ${units.length} 个单元`}
      </h3>
      {units.length === 0 ? (
        <p className={MUTED}>这个集合没有未覆盖的单元。</p>
      ) : (
        <table className={TABLE}>
          <thead>
            <tr>
              <th scope="col" className={TH}>
                剧
              </th>
              <th scope="col" className={TH}>
                geo
              </th>
              <th scope="col" className={TH}>
                原因
              </th>
            </tr>
          </thead>
          <tbody>
            {units.map((u) => (
              <tr key={`${u.identity}|${u.geo}`}>
                <td className={TD}>
                  <IdentityLink req={req} identity={u.identity} setId={setId}>
                    {identityLabel(u.identity)}
                  </IdentityLink>
                </td>
                <td className={TD}>{u.geo}</td>
                <td className={TD}>
                  {textOf(UNCOVERED_REASON_TEXT, u.reason)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

function SetBody({
  set,
  page,
  req,
}: {
  set: ObsSet;
  page: ObsDiscoveryPage | null;
  req: PickRequest;
}) {
  if (set.summary.channel !== "trends") return null;
  return (
    <>
      <section className={SECTION} data-obs-section="summary">
        <h3 className={HEADING}>这个集合的覆盖</h3>
        <SummaryLines summary={set.summary} />
      </section>
      {page ? <Discoveries page={page} req={req} /> : null}
      <Uncovered setId={set.set_id} summary={set.summary} req={req} />
    </>
  );
}

export function ObsTrendsView({
  data,
  req,
  now,
}: {
  data: TrendsData;
  req: PickRequest;
  now: Date;
}) {
  const set = data.trends.shown;
  return (
    <div data-obs-view="trends">
      <BOnlyNote />
      <ChannelHead load={data.trends} req={req} now={now} />
      {set ? <SetBody set={set} page={data.discoveries} req={req} /> : null}
      <p className={MUTED}>{DATA_SOURCE_TRENDS}</p>
    </div>
  );
}
