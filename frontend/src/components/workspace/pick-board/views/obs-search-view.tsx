// 工作台新建（TR-24）：资料页的「搜索表现（GSC）」tab（设计 5.4、5.6、5.7、5.8）。三层覆盖数字常驻：已收明细守恒、
// 明细缺口、不可知部分；窗口写明共同截止、24 小时窗口完整还是暂定、两个窗口的全站层准入；逐剧核对的计数含复用。
// 判定行按集合冻结的市场表分组（全球即全站合计在前，保加利亚单列，不在任何分组的国家最后），只列命中了标签的行，正式在前；
// 其余行只计数，详情页可看。没有集合、没有行、没有覆盖数字都写成一句话，不写成 0。
import { findMarketMap, groupRows } from "@/core/pick/obs-market-map";
import type {
  ObsCoverageLayers,
  ObsGscSummary,
  ObsSet,
  ObsState,
} from "@/core/pick/obs-rows";
import {
  countText,
  METRIC_TEXT,
  SITE_ADMISSION_TEXT,
  textOf,
  UNATTRIBUTED_TEXT,
} from "@/core/pick/obs-wording";
import type { PickRequest } from "@/core/pick-board/request";
import type { ObsStatePage, ObsTabData } from "@/server/pick-board";

import { GscRowCard } from "./obs-gsc-rows";
import {
  at,
  ChannelHead,
  HEADING,
  MUTED,
  SECTION,
  SUBHEADING,
  TABLE,
  TD,
  TH,
} from "./obs-parts";

type SearchData = Extract<ObsTabData, { kind: "search" }>;

function WindowsSection({ summary }: { summary: ObsGscSummary }) {
  return (
    <section className={SECTION} data-obs-section="windows">
      <h3 className={HEADING}>窗口与全站层准入</h3>
      <p>
        共同截止 {at(summary.cutoff)}
        {summary.cutoff_carried ? "（本轮缺水位，沿用上一个集合的截止）" : ""}
        ；截止之后的小时标「暂定」，不参与比较。
      </p>
      <p>
        24 小时窗口：
        {summary.formal_24h_window
          ? "完整（截止前 48 小时被可用区间连续覆盖）"
          : "暂定（本轮没有正式 24 小时窗口，24 小时行只出描述性标签）"}
        ；7 天窗口：最近 7 个完整 PT 日。
      </p>
      <p>
        全站层准入：24 小时{" "}
        {textOf(SITE_ADMISSION_TEXT, summary.site_admission_24h)}；7 天{" "}
        {textOf(SITE_ADMISSION_TEXT, summary.site_admission_7d)}。
      </p>
    </section>
  );
}

function unattributedText(layer: ObsCoverageLayers): string {
  const kinds = Object.entries(layer.unattributed);
  return kinds.length === 0
    ? "没有未归因的明细"
    : kinds
        .map(([kind, n]) => `${textOf(UNATTRIBUTED_TEXT, kind)} ${n}`)
        .join("、");
}

function LayerRow({ layer }: { layer: ObsCoverageLayers }) {
  return (
    <tr data-obs-layer={layer.metric}>
      <td className={TD}>{textOf(METRIC_TEXT, layer.metric)}</td>
      <td className={TD}>
        收到 {layer.received} = 已归因 {layer.attributed} + 未归因（
        {unattributedText(layer)}）
      </td>
      <td className={TD}>
        {layer.site_total === null
          ? "两种全站总量都没拿到，缺口算不出"
          : `全站总量 ${layer.site_total}，缺口 ${countText(layer.detail_gap)}`}
      </td>
    </tr>
  );
}

function CoverageSection({ summary }: { summary: ObsGscSummary }) {
  const u = summary.unknowable;
  return (
    <section className={SECTION} data-obs-section="coverage">
      <h3 className={HEADING}>覆盖三层</h3>
      {summary.layers.length === 0 ? (
        <p className={MUTED}>这个集合没有记下前两层的数字。</p>
      ) : (
        <table className={TABLE}>
          <thead>
            <tr>
              <th scope="col" className={TH}>指标</th>
              <th scope="col" className={TH}>第一层：已收明细守恒</th>
              <th scope="col" className={TH}>第二层：明细缺口</th>
            </tr>
          </thead>
          <tbody>
            {summary.layers.map((layer) => (
              <LayerRow key={layer.metric} layer={layer} />
            ))}
          </tbody>
        </table>
      )}
      <p className="mt-2">
        第三层（不可知部分）：截断的切片 {u.truncated_slices} 个、失败{" "}
        {u.failed_slices} 个、陈旧 {u.stale_slices} 个，覆盖 {u.hours} 个小时。
      </p>
    </section>
  );
}

function VcheckSection({ summary }: { summary: ObsGscSummary }) {
  const v = summary.vcheck_summary;
  return (
    <section className={SECTION} data-obs-section="vchecks">
      <h3 className={HEADING}>逐剧核对</h3>
      {v.requests === 0 ? (
        <p className={MUTED}>这一轮没有发逐剧核对请求。</p>
      ) : (
        <p>
          请求 {v.requests} 个（含复用 {v.reused} 个）：成功 {v.succeeded}、失败{" "}
          {v.failed}、截断 {v.truncated}、陈旧 {v.stale}、正则超长{" "}
          {v.regex_overflow}。
        </p>
      )}
      <p className={MUTED}>
        本轮 GSC 请求 {summary.requests} 个，配额错误 {summary.quota_errors}{" "}
        个。
      </p>
    </section>
  );
}

type RowGroup = Readonly<{
  key: string;
  label: string;
  note: string | null;
  rows: ObsState[];
}>;

/** By the set's frozen market map (its "global" group holds the site total); an unknown map version groups nothing. */
function rowGroups(set: ObsSet, rows: readonly ObsState[]): RowGroup[] {
  const version = set.frozen_inputs.market_map_version;
  const map = findMarketMap(version);
  if (map) return groupRows(map, rows);
  const ungrouped: RowGroup[] = [
    {
      key: "ALL",
      label: "全站合计",
      note: null,
      rows: rows.filter((row) => row.scope === "ALL"),
    },
    {
      key: "ungrouped",
      label: `按国家（市场表 ${version} 本页没有登记，不分组）`,
      note: null,
      rows: rows.filter((row) => row.scope !== "ALL"),
    },
  ];
  return ungrouped.filter((group) => group.rows.length > 0);
}

function countLine(page: ObsStatePage): string {
  if (page.total === 0) return "这个集合没有判定行。";
  const rest = page.total - page.labeled;
  const listed =
    page.labeled === 0
      ? `这个集合的 ${page.total} 行判定都没有命中任何标签。`
      : `这个集合共有 ${page.total} 行判定，其中 ${page.labeled} 行命中了标签，按市场分组列在下面（正式在前）。`;
  return rest > 0 && page.labeled > 0
    ? `${listed}其余 ${rest} 行没有命中标签，打开剧的详情页可看。`
    : listed;
}

function RowsSection({
  set,
  summary,
  page,
  req,
}: {
  set: ObsSet;
  summary: ObsGscSummary;
  page: ObsStatePage;
  req: PickRequest;
}) {
  return (
    <section className={SECTION} data-obs-section="states">
      <h3 className={HEADING}>判定行</h3>
      <p>{countLine(page)}</p>
      {rowGroups(set, page.rows).map((group) => (
        <div key={group.key} data-obs-group={group.key}>
          <h4 className={SUBHEADING}>
            {group.label}
            {group.note ? (
              <span className={MUTED}>（{group.note}）</span>
            ) : null}
          </h4>
          <ul>
            {group.rows.map((row) => (
              <GscRowCard
                key={row.row_id}
                row={row}
                summary={summary}
                req={req}
              />
            ))}
          </ul>
        </div>
      ))}
      {page.truncated ? (
        <p className={MUTED}>
          只列出前 {page.rows.length} 行（正式标签在前）。
        </p>
      ) : null}
    </section>
  );
}

export function ObsSearchView({
  data,
  req,
  now,
}: {
  data: SearchData;
  req: PickRequest;
  now: Date;
}) {
  const set = data.gsc.shown;
  const summary = set?.summary.channel === "gsc" ? set.summary : null;
  return (
    <div data-obs-view="search">
      <ChannelHead load={data.gsc} req={req} now={now} />
      {set && summary ? (
        <>
          <WindowsSection summary={summary} />
          <CoverageSection summary={summary} />
          <VcheckSection summary={summary} />
          {data.states ? (
            <RowsSection
              set={set}
              summary={summary}
              page={data.states}
              req={req}
            />
          ) : null}
        </>
      ) : null}
    </div>
  );
}
